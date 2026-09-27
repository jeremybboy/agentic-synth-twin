#include <clap/clap.h>

#include <dlfcn.h>
#include <unistd.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

constexpr std::uint32_t sample_rate = 44100;
constexpr std::uint32_t block_size = 64;
constexpr std::uint32_t note_frames = sample_rate * 2;
constexpr std::uint32_t tail_frames = sample_rate / 2;
constexpr std::uint32_t total_frames = note_frames + tail_frames;
constexpr std::int16_t midi_key = 48;
constexpr int midi_velocity = 100;
constexpr double clap_velocity = midi_velocity / 127.0;

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
        "Agentic Synth Twin deterministic renderer",
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
        const auto *descriptor = factory->get_plugin_descriptor(factory, 0);
        if (!descriptor || !descriptor->id) {
            throw std::runtime_error("the first plugin descriptor is invalid");
        }
        plugin_ = factory->create_plugin(factory, host, descriptor->id);
        if (!plugin_ || !plugin_->init(plugin_)) {
            if (plugin_) {
                plugin_->destroy(plugin_);
            }
            plugin_ = nullptr;
            throw std::runtime_error("plugin creation or initialization failed");
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

    void start() {
        if (!plugin_->activate(plugin_, sample_rate, 1, block_size)) {
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
    bool active_{false};
    bool processing_{false};
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

struct InputEvents {
    std::vector<const clap_event_header_t *> events;
    clap_input_events_t list{};

    InputEvents() {
        list.ctx = this;
        list.size = [](const clap_input_events_t *list) {
            const auto *self = static_cast<const InputEvents *>(list->ctx);
            return static_cast<std::uint32_t>(self->events.size());
        };
        list.get = [](const clap_input_events_t *list, std::uint32_t index) {
            const auto *self = static_cast<const InputEvents *>(list->ctx);
            return index < self->events.size() ? self->events[index] : nullptr;
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

std::vector<std::uint8_t> read_bytes(const std::filesystem::path &path) {
    std::ifstream stream(path, std::ios::binary);
    if (!stream) {
        throw std::runtime_error("could not open state file: " + path.string());
    }
    return {std::istreambuf_iterator<char>(stream), std::istreambuf_iterator<char>()};
}

void write_u16(std::ofstream &stream, std::uint16_t value) {
    const char bytes[] = {
        static_cast<char>(value & 0xFF),
        static_cast<char>((value >> 8) & 0xFF),
    };
    stream.write(bytes, sizeof(bytes));
}

void write_u32(std::ofstream &stream, std::uint32_t value) {
    const char bytes[] = {
        static_cast<char>(value & 0xFF),
        static_cast<char>((value >> 8) & 0xFF),
        static_cast<char>((value >> 16) & 0xFF),
        static_cast<char>((value >> 24) & 0xFF),
    };
    stream.write(bytes, sizeof(bytes));
}

struct RenderResult {
    double peak;
    std::uint64_t clipped_samples;
    struct AppliedParameter {
        clap_id id;
        double requested;
        double applied;
    };
    std::vector<AppliedParameter> parameter_changes;
};

struct ParameterChange {
    clap_id id;
    double value;
};

bool nearly_equal(double left, double right) {
    return std::abs(left - right) <=
           1e-9 * std::max({1.0, std::abs(left), std::abs(right)});
}

RenderResult render(const std::filesystem::path &plugin_path,
                    const std::filesystem::path &state_path,
                    const std::filesystem::path &wav_path,
                    const std::vector<ParameterChange> &parameter_changes) {
    PluginLibrary library(plugin_path);
    auto host = make_host();
    PluginInstance instance(library.factory(), &host);
    const auto *plugin = instance.get();
    const auto *state = static_cast<const clap_plugin_state_t *>(
        plugin->get_extension(plugin, CLAP_EXT_STATE));
    if (!state) {
        throw std::runtime_error("the plugin does not expose clap.state");
    }
    const auto state_bytes = read_bytes(state_path);
    if (state_bytes.empty()) {
        throw std::runtime_error("state file is empty");
    }
    MemoryInput state_input(state_bytes);
    if (!state->load(plugin, &state_input.stream)) {
        throw std::runtime_error("clap.state.load failed");
    }

    const clap_plugin_params_t *params = nullptr;
    std::vector<clap_param_info_t> changed_parameters;
    if (!parameter_changes.empty()) {
        params = static_cast<const clap_plugin_params_t *>(
            plugin->get_extension(plugin, CLAP_EXT_PARAMS));
        if (!params) {
            throw std::runtime_error("the plugin does not expose clap.params");
        }
        const auto count = params->count(plugin);
        changed_parameters.reserve(parameter_changes.size());
        for (std::size_t change_index = 0; change_index < parameter_changes.size();
             ++change_index) {
            const auto &change = parameter_changes[change_index];
            for (std::size_t previous = 0; previous < change_index; ++previous) {
                if (parameter_changes[previous].id == change.id) {
                    throw std::runtime_error("parameter ids must be unique");
                }
            }
            bool found = false;
            clap_param_info_t changed_parameter{};
            for (std::uint32_t index = 0; index < count; ++index) {
                clap_param_info_t info{};
                if (!params->get_info(plugin, index, &info)) {
                    throw std::runtime_error("clap.params.get_info failed");
                }
                if (info.id == change.id) {
                    changed_parameter = info;
                    found = true;
                    break;
                }
            }
            if (!found) {
                throw std::runtime_error("requested parameter id was not discovered");
            }
            if (change.value < changed_parameter.min_value ||
                change.value > changed_parameter.max_value) {
                throw std::runtime_error(
                    "requested parameter value is outside the discovered range");
            }
            changed_parameters.push_back(changed_parameter);
        }
    }

    std::vector<float> left(total_frames, 0.0F);
    std::vector<float> right(total_frames, 0.0F);
    OutputEvents output_events;
    instance.start();

    for (std::uint32_t frame = 0; frame < total_frames; frame += block_size) {
        const auto frames = std::min(block_size, total_frames - frame);
        clap_event_note_t note_on{};
        clap_event_note_t note_off{};
        std::vector<clap_event_param_value_t> parameter_events;
        InputEvents input_events;

        if (frame == 0) {
            parameter_events.reserve(parameter_changes.size());
            for (std::size_t index = 0; index < parameter_changes.size(); ++index) {
                const auto &change = parameter_changes[index];
                const auto &info = changed_parameters[index];
                clap_event_param_value_t parameter_event{};
                parameter_event.header.size = sizeof(parameter_event);
                parameter_event.header.time = 0;
                parameter_event.header.space_id = CLAP_CORE_EVENT_SPACE_ID;
                parameter_event.header.type = CLAP_EVENT_PARAM_VALUE;
                parameter_event.param_id = change.id;
                parameter_event.cookie = info.cookie;
                parameter_event.note_id = -1;
                parameter_event.port_index = -1;
                parameter_event.channel = -1;
                parameter_event.key = -1;
                parameter_event.value = change.value;
                parameter_events.push_back(parameter_event);
            }
            for (const auto &event : parameter_events) {
                input_events.events.push_back(&event.header);
            }
            note_on.header.size = sizeof(note_on);
            note_on.header.time = 0;
            note_on.header.space_id = CLAP_CORE_EVENT_SPACE_ID;
            note_on.header.type = CLAP_EVENT_NOTE_ON;
            note_on.note_id = 1;
            note_on.port_index = 0;
            note_on.channel = 0;
            note_on.key = midi_key;
            note_on.velocity = clap_velocity;
            input_events.events.push_back(&note_on.header);
        }
        if (frame <= note_frames && note_frames < frame + frames) {
            note_off.header.size = sizeof(note_off);
            note_off.header.time = note_frames - frame;
            note_off.header.space_id = CLAP_CORE_EVENT_SPACE_ID;
            note_off.header.type = CLAP_EVENT_NOTE_OFF;
            note_off.note_id = 1;
            note_off.port_index = 0;
            note_off.channel = 0;
            note_off.key = midi_key;
            note_off.velocity = 0.0;
            input_events.events.push_back(&note_off.header);
        }

        float *channels[] = {left.data() + frame, right.data() + frame};
        clap_audio_buffer_t audio_output{};
        audio_output.data32 = channels;
        audio_output.channel_count = 2;

        clap_process_t process{};
        process.steady_time = frame;
        process.frames_count = frames;
        process.audio_outputs = &audio_output;
        process.audio_outputs_count = 1;
        process.in_events = &input_events.list;
        process.out_events = &output_events.list;
        if (plugin->process(plugin, &process) == CLAP_PROCESS_ERROR) {
            throw std::runtime_error("plugin.process returned CLAP_PROCESS_ERROR");
        }
    }
    instance.stop();

    std::vector<RenderResult::AppliedParameter> applied_parameters;
    applied_parameters.reserve(parameter_changes.size());
    for (const auto &change : parameter_changes) {
        double applied = 0.0;
        if (!params->get_value(plugin, change.id, &applied)) {
            throw std::runtime_error("clap.params.get_value failed after render");
        }
        if (!nearly_equal(applied, change.value)) {
            throw std::runtime_error(
                "the plugin did not retain the requested parameter value");
        }
        applied_parameters.push_back({change.id, change.value, applied});
    }

    std::ofstream wav(wav_path, std::ios::binary);
    if (!wav) {
        throw std::runtime_error("could not open WAV output: " + wav_path.string());
    }
    constexpr std::uint16_t channels = 2;
    constexpr std::uint16_t bits_per_sample = 16;
    constexpr std::uint16_t block_align = channels * bits_per_sample / 8;
    constexpr std::uint32_t byte_rate = sample_rate * block_align;
    constexpr std::uint32_t data_bytes = total_frames * block_align;
    wav.write("RIFF", 4);
    write_u32(wav, 36 + data_bytes);
    wav.write("WAVEfmt ", 8);
    write_u32(wav, 16);
    write_u16(wav, 1);
    write_u16(wav, channels);
    write_u32(wav, sample_rate);
    write_u32(wav, byte_rate);
    write_u16(wav, block_align);
    write_u16(wav, bits_per_sample);
    wav.write("data", 4);
    write_u32(wav, data_bytes);

    double peak = 0.0;
    std::uint64_t clipped_samples = 0;
    for (std::uint32_t frame = 0; frame < total_frames; ++frame) {
        for (const float sample : {left[frame], right[frame]}) {
            peak = std::max(peak, std::abs(static_cast<double>(sample)));
            if (sample < -1.0F || sample > 1.0F) {
                ++clipped_samples;
            }
            const auto clipped = std::clamp(sample, -1.0F, 1.0F);
            const auto pcm = static_cast<std::int16_t>(std::lrint(clipped * 32767.0F));
            write_u16(wav, static_cast<std::uint16_t>(pcm));
        }
    }
    if (!wav) {
        throw std::runtime_error("failed while writing WAV output");
    }
    return {
        peak,
        clipped_samples,
        applied_parameters,
    };
}

clap_id parse_parameter_id(const char *text) {
    std::size_t consumed = 0;
    const auto parsed = std::stoul(text, &consumed, 10);
    if (text[consumed] != '\0' || parsed > CLAP_INVALID_ID - 1) {
        throw std::runtime_error("parameter id must be a valid unsigned integer");
    }
    return static_cast<clap_id>(parsed);
}

double parse_parameter_value(const char *text) {
    std::size_t consumed = 0;
    const auto parsed = std::stod(text, &consumed);
    if (text[consumed] != '\0' || !std::isfinite(parsed)) {
        throw std::runtime_error("parameter value must be a finite number");
    }
    return parsed;
}

} // namespace

int main(int argc, char **argv) {
    if (argc < 4 || (argc - 4) % 2 != 0) {
        std::cerr << "usage: clap-render PLUGIN.clap STATE.bin OUTPUT.wav "
                     "[PARAMETER_ID PARAMETER_VALUE ...]\n";
        return 64;
    }
    try {
        std::vector<ParameterChange> parameter_changes;
        for (int index = 4; index < argc; index += 2) {
            parameter_changes.push_back({
                parse_parameter_id(argv[index]),
                parse_parameter_value(argv[index + 1]),
            });
        }
        const auto result = [&]() {
            StdoutToStderr redirect;
            return render(argv[1], argv[2], argv[3], parameter_changes);
        }();
        std::cout << std::setprecision(17);
        std::cout << "{\"sample_rate\":" << sample_rate
                  << ",\"channels\":2,\"bits_per_sample\":16"
                  << ",\"midi_key\":" << midi_key
                  << ",\"velocity\":" << midi_velocity
                  << ",\"note_frames\":" << note_frames
                  << ",\"tail_frames\":" << tail_frames
                  << ",\"total_frames\":" << total_frames
                  << ",\"block_size\":" << block_size
                  << ",\"peak_float\":" << result.peak
                  << ",\"clipped_samples\":" << result.clipped_samples
                  << ",\"parameter_change\":";
        if (result.parameter_changes.size() == 1) {
            const auto &change = result.parameter_changes.front();
            std::cout << "{\"id\":" << change.id
                      << ",\"requested\":" << change.requested
                      << ",\"applied\":" << change.applied << "}";
        } else {
            std::cout << "null";
        }
        std::cout << ",\"parameter_changes\":[";
        for (std::size_t index = 0; index < result.parameter_changes.size(); ++index) {
            if (index != 0) {
                std::cout << ',';
            }
            const auto &change = result.parameter_changes[index];
            std::cout << "{\"id\":" << change.id
                      << ",\"requested\":" << change.requested
                      << ",\"applied\":" << change.applied << "}";
        }
        std::cout << "]}\n";
        return 0;
    } catch (const std::exception &error) {
        std::cerr << "clap-render: " << error.what() << '\n';
        return 1;
    }
}
