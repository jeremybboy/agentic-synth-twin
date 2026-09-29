#include <clap/clap.h>

#include <dlfcn.h>
#include <unistd.h>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

struct ProbeResult {
    std::string json;
    int exit_code;
};

class StdoutToStderr {
  public:
    StdoutToStderr() {
        std::fflush(stdout);
        saved_stdout_ = dup(STDOUT_FILENO);
        if (saved_stdout_ < 0 || dup2(STDERR_FILENO, STDOUT_FILENO) < 0) {
            if (saved_stdout_ >= 0) {
                close(saved_stdout_);
            }
            throw std::runtime_error("could not redirect plugin diagnostics");
        }
    }

    ~StdoutToStderr() {
        std::fflush(stdout);
        if (saved_stdout_ >= 0) {
            dup2(saved_stdout_, STDOUT_FILENO);
            close(saved_stdout_);
        }
    }

    StdoutToStderr(const StdoutToStderr &) = delete;
    StdoutToStderr &operator=(const StdoutToStderr &) = delete;

  private:
    int saved_stdout_{-1};
};

std::string json_escape(const char *text) {
    std::ostringstream out;
    for (const unsigned char character : std::string(text ? text : "")) {
        switch (character) {
        case '"': out << "\\\""; break;
        case '\\': out << "\\\\"; break;
        case '\b': out << "\\b"; break;
        case '\f': out << "\\f"; break;
        case '\n': out << "\\n"; break;
        case '\r': out << "\\r"; break;
        case '\t': out << "\\t"; break;
        default:
            if (character < 0x20) {
                out << "\\u" << std::hex << std::setw(4) << std::setfill('0')
                    << static_cast<int>(character) << std::dec;
            } else {
                out << character;
            }
        }
    }
    return out.str();
}

std::string base64_encode(const std::vector<std::uint8_t> &bytes) {
    static constexpr char alphabet[] =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string encoded;
    encoded.reserve(4 * ((bytes.size() + 2) / 3));
    for (std::size_t offset = 0; offset < bytes.size(); offset += 3) {
        const auto remaining = bytes.size() - offset;
        const std::uint32_t first = bytes[offset];
        const std::uint32_t second = remaining > 1 ? bytes[offset + 1] : 0;
        const std::uint32_t third = remaining > 2 ? bytes[offset + 2] : 0;
        const std::uint32_t value = (first << 16) | (second << 8) | third;
        encoded.push_back(alphabet[(value >> 18) & 0x3F]);
        encoded.push_back(alphabet[(value >> 12) & 0x3F]);
        encoded.push_back(remaining > 1 ? alphabet[(value >> 6) & 0x3F] : '=');
        encoded.push_back(remaining > 2 ? alphabet[value & 0x3F] : '=');
    }
    return encoded;
}

std::filesystem::path executable_path(const std::filesystem::path &plugin_path) {
    if (plugin_path.extension() != ".clap") {
        return plugin_path;
    }
    return plugin_path / "Contents" / "MacOS" / plugin_path.stem();
}

const void *host_get_extension(const clap_host_t *, const char *) { return nullptr; }
void host_request_restart(const clap_host_t *) {}
void host_request_process(const clap_host_t *) {}
void host_request_callback(const clap_host_t *) {}

clap_host_t make_host() {
    return {
        CLAP_VERSION,
        nullptr,
        "Agentic Synth Twin feasibility probe",
        "Agentic Synth Twin contributors",
        "https://github.com/jeremybboy/agentic-synth-twin",
        "0.1",
        host_get_extension,
        host_request_restart,
        host_request_process,
        host_request_callback,
    };
}

class PluginLibrary {
  public:
    explicit PluginLibrary(const std::filesystem::path &plugin_path)
        : plugin_path_(std::filesystem::absolute(plugin_path).lexically_normal()) {
        const auto binary = executable_path(plugin_path_);
        handle_ = dlopen(binary.c_str(), RTLD_LOCAL | RTLD_NOW);
        if (!handle_) {
            throw std::runtime_error("dlopen failed for " + binary.string() + ": " + dlerror());
        }
        entry_ = static_cast<const clap_plugin_entry_t *>(dlsym(handle_, "clap_entry"));
        if (!entry_) {
            throw std::runtime_error("the module does not export clap_entry");
        }
        if (!clap_version_is_compatible(entry_->clap_version)) {
            throw std::runtime_error("the plugin exposes an incompatible CLAP version");
        }
        if (!entry_->init(plugin_path_.c_str())) {
            throw std::runtime_error("clap_entry.init failed");
        }
        initialized_ = true;
    }

    ~PluginLibrary() {
        if (initialized_) {
            entry_->deinit();
        }
        if (handle_) {
            dlclose(handle_);
        }
    }

    const clap_plugin_factory_t *factory() const {
        const auto *factory = static_cast<const clap_plugin_factory_t *>(
            entry_->get_factory(CLAP_PLUGIN_FACTORY_ID));
        if (!factory) {
            throw std::runtime_error("the module does not expose a CLAP plugin factory");
        }
        return factory;
    }

  private:
    std::filesystem::path plugin_path_;
    void *handle_{nullptr};
    const clap_plugin_entry_t *entry_{nullptr};
    bool initialized_{false};
};

class PluginInstance {
  public:
    PluginInstance(const clap_plugin_factory_t *factory, const clap_host_t *host) {
        if (factory->get_plugin_count(factory) == 0) {
            throw std::runtime_error("the plugin factory is empty");
        }
        descriptor_ = factory->get_plugin_descriptor(factory, 0);
        if (!descriptor_ || !descriptor_->id) {
            throw std::runtime_error("the first plugin descriptor is invalid");
        }
        plugin_ = factory->create_plugin(factory, host, descriptor_->id);
        if (!plugin_) {
            throw std::runtime_error("the factory could not create the plugin");
        }
        if (!plugin_->init(plugin_)) {
            plugin_->destroy(plugin_);
            plugin_ = nullptr;
            throw std::runtime_error("plugin.init failed");
        }
    }

    ~PluginInstance() {
        if (!plugin_) {
            return;
        }
        if (processing_) {
            plugin_->stop_processing(plugin_);
        }
        if (active_) {
            plugin_->deactivate(plugin_);
        }
        plugin_->destroy(plugin_);
    }

    const clap_plugin_t *get() const { return plugin_; }
    const clap_plugin_descriptor_t *descriptor() const { return descriptor_; }

    void activate() {
        if (!plugin_->activate(plugin_, 44100.0, 64, 64)) {
            throw std::runtime_error("plugin.activate failed");
        }
        active_ = true;
        if (!plugin_->start_processing(plugin_)) {
            throw std::runtime_error("plugin.start_processing failed");
        }
        processing_ = true;
    }

    void stop() {
        if (processing_) {
            plugin_->stop_processing(plugin_);
            processing_ = false;
        }
        if (active_) {
            plugin_->deactivate(plugin_);
            active_ = false;
        }
    }

  private:
    const clap_plugin_t *plugin_{nullptr};
    const clap_plugin_descriptor_t *descriptor_{nullptr};
    bool active_{false};
    bool processing_{false};
};

struct MemoryOutput {
    std::vector<std::uint8_t> bytes;
    clap_ostream_t stream{};

    MemoryOutput() {
        stream.ctx = this;
        stream.write = [](const clap_ostream_t *stream, const void *buffer, uint64_t size) {
            auto *self = static_cast<MemoryOutput *>(stream->ctx);
            const auto *begin = static_cast<const std::uint8_t *>(buffer);
            self->bytes.insert(self->bytes.end(), begin, begin + size);
            return static_cast<int64_t>(size);
        };
    }
};

struct MemoryInput {
    const std::vector<std::uint8_t> &bytes;
    std::size_t offset{0};
    clap_istream_t stream{};

    explicit MemoryInput(const std::vector<std::uint8_t> &source) : bytes(source) {
        stream.ctx = this;
        stream.read = [](const clap_istream_t *stream, void *buffer, uint64_t size) {
            auto *self = static_cast<MemoryInput *>(stream->ctx);
            const auto available = self->bytes.size() - self->offset;
            const auto count = std::min<std::size_t>(available, size);
            if (count == 0) {
                return int64_t{0};
            }
            std::memcpy(buffer, self->bytes.data() + self->offset, count);
            self->offset += count;
            return static_cast<int64_t>(count);
        };
    }
};

std::vector<std::uint8_t> read_bytes(const std::filesystem::path &path) {
    std::ifstream stream(path, std::ios::binary);
    if (!stream) {
        throw std::runtime_error("could not open state file: " + path.string());
    }
    return {std::istreambuf_iterator<char>(stream), std::istreambuf_iterator<char>()};
}

struct OneInputEvent {
    const clap_event_header_t *event;
    clap_input_events_t list{};

    explicit OneInputEvent(const clap_event_header_t *source) : event(source) {
        list.ctx = this;
        list.size = [](const clap_input_events_t *) { return uint32_t{1}; };
        list.get = [](const clap_input_events_t *list, uint32_t index) {
            const auto *self = static_cast<const OneInputEvent *>(list->ctx);
            return index == 0 ? self->event : nullptr;
        };
    }
};

struct OutputEvents {
    clap_output_events_t list{};

    OutputEvents() {
        list.ctx = this;
        list.try_push = [](const clap_output_events_t *, const clap_event_header_t *) {
            return true;
        };
    }
};

void process_parameter(const clap_plugin_t *plugin, const clap_param_info_t &info, double value) {
    clap_event_param_value_t event{};
    event.header.size = sizeof(event);
    event.header.time = 0;
    event.header.space_id = CLAP_CORE_EVENT_SPACE_ID;
    event.header.type = CLAP_EVENT_PARAM_VALUE;
    event.param_id = info.id;
    event.cookie = info.cookie;
    event.note_id = -1;
    event.port_index = -1;
    event.channel = -1;
    event.key = -1;
    event.value = value;

    OneInputEvent input(&event.header);
    OutputEvents output;
    std::vector<float> left(64, 0.0F);
    std::vector<float> right(64, 0.0F);
    float *channels[] = {left.data(), right.data()};
    clap_audio_buffer_t audio{};
    audio.data32 = channels;
    audio.channel_count = 2;

    clap_process_t process{};
    process.steady_time = 0;
    process.frames_count = 64;
    process.audio_outputs = &audio;
    process.audio_outputs_count = 1;
    process.in_events = &input.list;
    process.out_events = &output.list;
    const auto status = plugin->process(plugin, &process);
    if (status == CLAP_PROCESS_ERROR) {
        throw std::runtime_error("plugin.process rejected the parameter event");
    }
}

bool nearly_equal(double left, double right) {
    return std::abs(left - right) <= 1e-9 * std::max({1.0, std::abs(left), std::abs(right)});
}

double mutation_value(const clap_param_info_t &info, double current) {
    if (nearly_equal(current, info.max_value)) {
        return info.min_value;
    }
    return info.max_value;
}

ProbeResult run(const std::filesystem::path &plugin_path,
                const std::optional<std::filesystem::path> &state_path,
                bool inspect_only) {
    PluginLibrary library(plugin_path);
    const auto *factory = library.factory();
    auto host = make_host();
    PluginInstance instance(factory, &host);
    const auto *plugin = instance.get();
    const auto *params = static_cast<const clap_plugin_params_t *>(
        plugin->get_extension(plugin, CLAP_EXT_PARAMS));
    const auto *state = static_cast<const clap_plugin_state_t *>(
        plugin->get_extension(plugin, CLAP_EXT_STATE));
    if (!params) {
        throw std::runtime_error("the plugin does not expose clap.params");
    }
    if (!state) {
        throw std::runtime_error("the plugin does not expose clap.state");
    }

    std::vector<std::uint8_t> loaded_state;
    if (state_path) {
        loaded_state = read_bytes(*state_path);
        if (loaded_state.empty()) {
            throw std::runtime_error("state file is empty");
        }
        MemoryInput state_input(loaded_state);
        if (!state->load(plugin, &state_input.stream)) {
            throw std::runtime_error("clap.state.load failed");
        }
    }

    std::vector<clap_param_info_t> inventory;
    const auto count = params->count(plugin);
    for (uint32_t index = 0; index < count; ++index) {
        clap_param_info_t info{};
        if (!params->get_info(plugin, index, &info)) {
            throw std::runtime_error("clap.params.get_info failed at index " +
                                     std::to_string(index));
        }
        inventory.push_back(info);
    }
    if (inventory.empty()) {
        throw std::runtime_error("the plugin reported no parameters");
    }

    const auto selected = std::find_if(inventory.begin(), inventory.end(), [](const auto &info) {
        return info.max_value > info.min_value;
    });
    if (selected == inventory.end()) {
        throw std::runtime_error("no mutable parameter was discovered");
    }
    double before = 0.0;
    if (!params->get_value(plugin, selected->id, &before)) {
        throw std::runtime_error("clap.params.get_value failed before mutation");
    }

    MemoryOutput saved_state;
    if (!state->save(plugin, &saved_state.stream) || saved_state.bytes.empty()) {
        throw std::runtime_error("clap.state.save failed or returned no bytes");
    }

    double requested = before;
    double after = before;
    double restored = before;
    bool changed = false;
    bool restored_ok = true;
    if (!inspect_only) {
        requested = mutation_value(*selected, before);
        instance.activate();
        process_parameter(plugin, *selected, requested);
        instance.stop();

        if (!params->get_value(plugin, selected->id, &after)) {
            throw std::runtime_error("clap.params.get_value failed after mutation");
        }

        MemoryInput restore_stream(saved_state.bytes);
        if (!state->load(plugin, &restore_stream.stream)) {
            throw std::runtime_error("clap.state.load failed");
        }
        if (!params->get_value(plugin, selected->id, &restored)) {
            throw std::runtime_error("clap.params.get_value failed after state restore");
        }

        changed = nearly_equal(after, requested) && !nearly_equal(after, before);
        restored_ok = nearly_equal(restored, before);
    }

    const auto *descriptor = instance.descriptor();
    std::ostringstream output;
    output << std::setprecision(17);
    output << "{\n"
           << "  \"plugin\": {\"id\": \"" << json_escape(descriptor->id)
           << "\", \"name\": \"" << json_escape(descriptor->name)
           << "\", \"vendor\": \"" << json_escape(descriptor->vendor)
           << "\", \"version\": \"" << json_escape(descriptor->version) << "\"},\n"
           << "  \"parameter_count\": " << inventory.size() << ",\n"
           << "  \"parameters\": [\n";
    for (std::size_t index = 0; index < inventory.size(); ++index) {
        const auto &info = inventory[index];
        double current = 0.0;
        if (!params->get_value(plugin, info.id, &current)) {
            throw std::runtime_error("clap.params.get_value failed while writing inventory");
        }
        char current_text[CLAP_NAME_SIZE]{};
        const bool has_current_text = params->value_to_text &&
            params->value_to_text(plugin, info.id, current, current_text,
                                  sizeof(current_text));
        output << "    {\"index\": " << index << ", \"id\": " << info.id
               << ", \"name\": \"" << json_escape(info.name)
               << "\", \"module\": \"" << json_escape(info.module)
               << "\", \"min\": " << info.min_value << ", \"max\": "
               << info.max_value << ", \"default\": " << info.default_value
               << ", \"current\": " << current << ", \"current_text\": ";
        if (has_current_text) {
            output << "\"" << json_escape(current_text) << "\"";
        } else {
            output << "null";
        }
        output << ", \"flags\": " << info.flags
               << ", \"automatable\": "
               << ((info.flags & CLAP_PARAM_IS_AUTOMATABLE) ? "true" : "false")
               << ", \"modulatable\": "
               << ((info.flags & CLAP_PARAM_IS_MODULATABLE) ? "true" : "false") << "}";
        output << (index + 1 == inventory.size() ? "\n" : ",\n");
    }
    output << "  ],\n"
           << "  \"state_bytes\": " << saved_state.bytes.size() << ",\n"
           << "  \"state_base64\": \"" << base64_encode(saved_state.bytes) << "\",\n"
           << "  \"mutation\": ";
    if (inspect_only) {
        output << "null\n";
    } else {
        output << "{\"parameter_id\": " << selected->id
               << ", \"parameter_name\": \"" << json_escape(selected->name)
               << "\", \"before\": " << before << ", \"requested\": " << requested
               << ", \"after\": " << after << ", \"restored\": " << restored
               << ", \"changed\": " << (changed ? "true" : "false")
               << ", \"restore_verified\": " << (restored_ok ? "true" : "false")
               << "}\n";
    }
    output
           << "}\n";

    return {output.str(), inspect_only || (changed && restored_ok) ? 0 : 2};
}

} // namespace

int main(int argc, char **argv) {
    if (argc < 2) {
        std::cerr << "usage: clap-probe /path/to/plugin.clap "
                     "[--state STATE.bin] [--inspect-only]\n";
        return 64;
    }
    try {
        std::optional<std::filesystem::path> state_path;
        bool inspect_only = false;
        for (int index = 2; index < argc; ++index) {
            const std::string option = argv[index];
            if (option == "--state") {
                if (++index >= argc) {
                    throw std::runtime_error("--state requires a path");
                }
                state_path = argv[index];
            } else if (option == "--inspect-only") {
                inspect_only = true;
            } else {
                throw std::runtime_error("unknown option: " + option);
            }
        }
        const auto result = [&]() {
            StdoutToStderr redirect;
            return run(argv[1], state_path, inspect_only);
        }();
        std::cout << result.json;
        return result.exit_code;
    } catch (const std::exception &error) {
        std::cerr << "clap-probe: " << error.what() << '\n';
        return 1;
    }
}
