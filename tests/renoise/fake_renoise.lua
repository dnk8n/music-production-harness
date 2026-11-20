-- A fake of the parts of the Renoise Lua API the bridge tool uses, for tests.
-- It records state so a test can print the song the tool built.
--
-- It also keeps track of unsaved changes the way that is hardest on the tool
-- (REQ-REN-08). A tick is one step of Renoise's event loop. Any write to a
-- song during a tick counts as unsaved once the tick is over, even if the
-- tool saved the song during that tick; a save in a tick with no writes
-- leaves nothing unsaved. Starting a new song while the open one has unsaved
-- changes makes Renoise ask whether to save it. Nobody answers in a test, so
-- the question is recorded in `prompts` and the new song never starts.
--
-- Its mixer devices have the parameters Renoise 3.5.4 has, named and shown
-- as scripts/renoise-check listed them on Dean's Mac (2026-11-20): a
-- frequency as "1.20 kHz", a level as "-6.021 dB" or "-INF dB", a share as
-- "40.000 %", a pan as "25 L". Their scales are this fake's own, since the
-- tool only goes by what they show. A #Send's mode is in its preset data, as
-- in Renoise; this fake's #Send starts with its source muted, the harder case
-- for the tool, because Renoise's own default isn't known yet.

local fake = { renders = {}, writes = 0 }

-- A table whose field writes count as changes to the song.
local function watched(object)
  return setmetatable({}, {
    __index = object,
    __newindex = function(_, key, value)
      object[key] = value
      fake.writes = fake.writes + 1
    end,
  })
end

-- How parameters show their values.
local function khz(value) -- 0 to 1: 20 Hz to 20 kHz, evenly in octaves, always in kHz
  return string.format("%.2f kHz", 20 * 1000 ^ value / 1000)
end

local function percent(value) -- a share: 1 is 100 %
  return string.format("%.3f %%", value * 100)
end

local function pan(value) -- 0 to 1: hard left to hard right
  local side = math.floor((value - 0.5) * 100 + 0.5)
  if side == 0 then
    return "Center"
  end
  return string.format("%d %s", math.abs(side), side < 0 and "L" or "R")
end

local function whole_ms(low, high) -- 0 to 1: low to high ms, evenly in decades
  return function(value)
    return string.format("%d ms", math.floor(low * (high / low) ^ value + 0.5))
  end
end

local function db(value) -- a linear gain
  if value <= 0 then
    return "-INF dB"
  end
  return string.format("%.3f dB", 20 * math.log10(value))
end

local function ms(value) -- 0 to 1: 1 ms to 10 s, evenly in decades
  local t = 10 ^ (value * 4)
  if t < 1000 then
    return string.format("%.1f ms", t)
  end
  return string.format("%.2f s", t / 1000)
end

local function number(value)
  return string.format("%.3f", value)
end

local function whole(value)
  return string.format("%d", math.floor(value + 0.5))
end

local function choice(names)
  return function(value)
    return names[math.floor(value + 0.5) + 1]
  end
end

local onoff = choice({ "Off", "On" })

-- A parameter: { name, min, max, default, quantum, show }.
local function new_parameter(spec)
  local state = {
    name = spec[1],
    value_min = spec[2],
    value_max = spec[3],
    value_default = spec[4],
    value_quantum = spec[5],
    value = spec[4],
  }
  local show = spec[6]
  return setmetatable({}, {
    __index = function(_, key)
      if key == "value_string" then
        return show(state.value)
      end
      return state[key]
    end,
    __newindex = function(_, key, value)
      assert(key == "value", "can't set a parameter's " .. key)
      assert(value >= state.value_min - 1e-9 and value <= state.value_max + 1e-9, "out of range")
      if state.value_quantum > 0 then
        local steps = math.floor((value - state.value_min) / state.value_quantum + 0.5)
        value = state.value_min + steps * state.value_quantum
      end
      state.value = value
      fake.writes = fake.writes + 1
    end,
  })
end

local function reads(text) -- a parameter that always shows the same
  return function()
    return text
  end
end

-- Each native device's parameters, as Renoise 3.5.4 names them (only those the
-- tool might touch). The filter's model is in its preset data, and the reverb
-- has no damping, so a plan's optional damping is missing, as it is in Renoise.
local DEVICES = {
  ["Audio/Effects/Native/Digital Filter"] = function()
    return {
      { "Type", 0, 3, 0, 1, choice({ "LowPass", "BandPass", "BandStop", "HighPass" }) },
      { "Cutoff", 0, 1, 1, 0, khz },
      { "Resonance", 0, 1, 0.125, 0, percent },
    }
  end,
  ["Audio/Effects/Native/Gainer"] = function()
    return {
      { "Gain", 0, 4, 1, 0, db },
      { "Panning", 0, 1, 0.5, 0, pan },
    }
  end,
  ["Audio/Effects/Native/#Send"] = function()
    return {
      { "Amount", 0, 1, 1, 0, db },
      { "Panning", 0, 1, 0.5, 0, pan },
      { "Receiver", 0, 255, 0, 1, whole }, -- the send track's number, from 0
    }
  end,
  ["Audio/Effects/Native/Multitap"] = function()
    local found = { { "No Panic", 0, 255, 0, 1, whole }, { "Mute Dry", 0, 1, 0, 1, onoff } }
    for tap = 1, 4 do
      local t = "T" .. tap .. " "
      found[#found + 1] = { t .. "Dly Left", 0, 1, 0.5, 0, ms }
      found[#found + 1] = { t .. "Dly Right", 0, 1, 0.5, 0, ms }
      found[#found + 1] = { t .. "Amount", 0, 1, tap == 1 and 1 or 0, 0, db }
      found[#found + 1] = { t .. "L Feedback", -1, 1, 0.5, 0, percent }
      found[#found + 1] = { t .. "R Feedback", -1, 1, 0.5, 0, percent }
      found[#found + 1] = { t .. "Ping Pong", 0, 1, 0, 1, onoff }
      found[#found + 1] =
        { t .. "Flt Mode", 0, 4, 3, 1, choice({ "Off", "Input", "Feedback", "In + Feed", "Output" }) }
      found[#found + 1] =
        { t .. "Flt Type", 0, 3, 0, 1, choice({ "Lowpass", "Highpass", "Bandpass", "Notch" }) }
      found[#found + 1] = { t .. "Flt Freq.", 0, 1, 1, 0, khz }
      found[#found + 1] = { t .. "Line Sync", 0, 1, 0, 1, onoff }
      -- A pattern command's target: Renoise always shows it as -1.
      found[#found + 1] = { t .. "Sync L DelayTime", 0, 255, 0, 1, reads("-1") }
      found[#found + 1] = { t .. "Sync R DelayTime", 0, 255, 0, 1, reads("-1") }
      found[#found + 1] = { t .. "L Sync Time", 0, 255, 1, 1, whole } -- lines, with line sync
      found[#found + 1] = { t .. "R Sync Time", 0, 255, 3, 1, whole }
    end
    return found
  end,
  ["Audio/Effects/Native/mpReverb 2"] = function()
    return {
      { "Duration", 0, 1, 0.75, 0, whole_ms(100, 10000) },
      { "Predelay", 0, 1, 0.04, 0, whole_ms(1, 251) }, -- Renoise's starts at 0 ms
      { "Wet Mix", 0, 1, 0.4, 0, percent },
      { "Dry Mix", 0, 1, 1, 0, percent },
    }
  end,
}

-- Settings a device keeps in its preset data rather than in a parameter, in
-- the order the data lists them.
local PRESETS = {
  ["Audio/Effects/Native/Digital Filter"] = {
    { "IsMaximized", "true" },
    { "OversamplingFactor", "2x" },
    { "Model", "Biquad" },
    { "ShowResponseView", "true" },
    { "ResponseViewMaxGain", "18" },
  },
  ["Audio/Effects/Native/#Send"] = {
    { "MuteSource", "true" },
    { "SmoothParameterChanges", "true" },
    { "ApplyPostVolume", "true" },
  },
}

-- The values a preset setting can take, where they're a fixed list (the
-- filter's models, from Renoise's manual): data with any other isn't loaded.
local PRESET_CHOICES = {
  Model = {
    Biquad = true,
    ["Butterworth 4n"] = true,
    ["Butterworth 8n"] = true,
    ["Chebyshev 4n"] = true,
    ["Chebyshev 8n"] = true,
  },
}

-- A device's preset data, as XML like Renoise's: its active state, each
-- parameter's value, then its other settings.
local function preset_xml(path, parameters, settings)
  local lines = {
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<FilterDevicePreset doc_version="13">',
    '  <DeviceSlot type="' .. path:match("[^/]+$") .. '">',
    "    <IsActive>",
    "      <Value>1.0</Value>",
    "      <Visualization>Device only</Visualization>",
    "    </IsActive>",
  }
  for k, parameter in ipairs(parameters) do
    lines[#lines + 1] = string.format("    <Parameter%d>", k)
    lines[#lines + 1] = string.format("      <Value>%s</Value>", tostring(parameter.value))
    lines[#lines + 1] = "    </Parameter" .. k .. ">"
  end
  for _, setting in ipairs(settings) do
    lines[#lines + 1] = string.format("    <%s>%s</%s>", setting[1], setting[2], setting[1])
  end
  lines[#lines + 1] = "  </DeviceSlot>"
  lines[#lines + 1] = "</FilterDevicePreset>"
  return table.concat(lines, "\n")
end

local function new_device(path, song)
  local make = DEVICES[path]
  assert(make, "Renoise has no device " .. path)
  local parameters = {}
  for _, spec in ipairs(make(song)) do
    parameters[#parameters + 1] = new_parameter(spec)
  end
  local settings = {}
  for k, setting in ipairs(PRESETS[path] or {}) do
    settings[k] = { setting[1], setting[2] }
  end
  local object = {
    name = path,
    device_path = path,
    display_name = "",
    is_active = true,
    parameters = parameters,
  }
  return setmetatable({}, {
    __index = function(_, key)
      if key == "active_preset_data" then
        return preset_xml(path, parameters, settings)
      end
      return object[key]
    end,
    __newindex = function(_, key, value)
      if key == "active_preset_data" then -- Renoise loads it: each setting from its tag
        local loaded = {}
        for k, setting in ipairs(settings) do
          local found = value:match("<" .. setting[1] .. ">([^<]*)</" .. setting[1] .. ">")
          assert(found, "preset data without " .. setting[1])
          local choices = PRESET_CHOICES[setting[1]]
          assert(choices == nil or choices[found], "no " .. setting[1] .. " called " .. found)
          loaded[k] = found
        end
        for k, setting in ipairs(settings) do
          setting[2] = loaded[k]
        end
      else
        object[key] = value
      end
      fake.writes = fake.writes + 1
    end,
  })
end

local function new_column()
  return { note_value = 121, instrument_value = 255, volume_value = 255, delay_value = 0 }
end

-- An automation; its points are checked as Renoise checks them.
local function new_automation(parameter, pattern)
  local state = { dest_parameter = parameter, playmode = 1, points = {} }
  return setmetatable({}, {
    __index = function(_, key)
      if key == "length" then
        return pattern.number_of_lines
      end
      return state[key]
    end,
    __newindex = function(_, key, value)
      if key == "points" then
        local seen = {}
        for _, point in ipairs(value) do
          local time = math.floor(point.time * 256 + 0.5)
          assert(not seen[time], "two automation points at one time")
          assert(time >= 256 and time < (pattern.number_of_lines + 1) * 256, "point out of the pattern")
          assert(point.value >= 0 and point.value <= 1, "automation value out of range")
          seen[time] = true
        end
      end
      state[key] = value
      fake.writes = fake.writes + 1
    end,
  })
end

local function new_pattern()
  local pattern = { number_of_lines = 64, tracks = {} }
  function pattern:clear()
    self.tracks = {}
  end
  function pattern:track(index)
    self.tracks[index] = self.tracks[index] or { lines = {}, automation = {} }
    local track = self.tracks[index]
    function track:line(line_index)
      assert(line_index >= 1 and line_index <= pattern.number_of_lines, "line out of range")
      self.lines[line_index] = self.lines[line_index] or { columns = {}, effects = {} }
      local line = self.lines[line_index]
      function line:note_column(column)
        self.columns[column] = self.columns[column] or new_column()
        return self.columns[column]
      end
      function line:effect_column(column)
        self.effects[column] = self.effects[column] or { number_string = "00", amount_value = 0 }
        return self.effects[column]
      end
      return line
    end
    function track:find_automation(parameter)
      return self.automation[parameter]
    end
    function track:create_automation(parameter)
      assert(not self.automation[parameter], "already automated")
      self.automation[parameter] = new_automation(parameter, pattern)
      return self.automation[parameter]
    end
    return track
  end
  return pattern
end

local function new_sample()
  local sample = {
    name = "",
    volume = 1.0,
    transpose = 0,
    fine_tune = 0,
    interpolation_mode = 3,
    new_note_action = 1,
    modulation_set_index = 0,
    slice_markers = {},
    autoseek = false,
    autofade = false,
    sample_mapping = { base_note = 48, note_range = { 0, 119 } },
    sample_buffer = {},
  }
  function sample.sample_buffer:load_from(path)
    local file = io.open(path, "rb")
    if not file then
      return false
    end
    file:close()
    sample.loaded = path
    return true
  end
  return sample
end

local function new_envelope()
  local envelope = {
    name = "AHDSR",
    display_name = "",
    tempo_sync_switching_allowed = true,
    tempo_synced = true,
    attack = new_parameter({ "Attack", 0, 1, 0.1, 0, ms }),
    hold = new_parameter({ "Hold", 0, 1, 0, 0, ms }),
    duration = new_parameter({ "Decay", 0, 1, 0.5, 0, ms }),
    sustain = new_parameter({ "Sustain", 0, 1, 0.5, 0, number }),
    release = new_parameter({ "Release", 0, 1, 0.5, 0, ms }),
  }
  envelope.parameters =
    { envelope.attack, envelope.hold, envelope.duration, envelope.sustain, envelope.release }
  return envelope
end

local function new_modulation_set()
  local set = { name = "", devices = {}, available_devices = { "Modulation/AHDSR" } }
  function set:init()
    self.devices = {}
  end
  function set:insert_device_at(path, target, index)
    assert(path == "Modulation/AHDSR", "this fake only has an AHDSR")
    local device = new_envelope()
    device.target = target
    table.insert(self.devices, index, device)
    return device
  end
  return set
end

local function new_instrument()
  local instrument = { name = "", samples = {}, sample_modulation_sets = {} }
  function instrument:insert_sample_at(index)
    local sample = new_sample()
    function sample.insert_slice_marker(this, position)
      this.slice_markers[#this.slice_markers + 1] = position
      local alias = new_sample()
      alias.sample_mapping.base_note = 48 + #this.slice_markers
      instrument.samples[#instrument.samples + 1] = alias
    end
    table.insert(self.samples, index, sample)
    return sample
  end
  function instrument:delete_sample_at(index)
    table.remove(self.samples, index)
  end
  function instrument:insert_sample_modulation_set_at(index)
    table.insert(self.sample_modulation_sets, index, new_modulation_set())
  end
  return instrument
end

local function new_track(name, song, kind)
  local track = {
    name = name,
    type = kind or 1,
    mute_state = 1,
    devices = { { name = "TrackVolPan", is_active = true, parameters = {} } },
    visible_note_columns = 1,
    visible_effect_columns = 0,
    volume_column_visible = false,
    delay_column_visible = false,
    available_devices = {},
  }
  for path in pairs(DEVICES) do
    track.available_devices[#track.available_devices + 1] = path
  end
  table.sort(track.available_devices)
  function track:insert_device_at(path, index)
    assert(index >= 2, "nothing goes before a track's mixer device")
    local device = new_device(path, song)
    table.insert(self.devices, index, device)
    return device
  end
  function track:device(index)
    return self.devices[index]
  end
  function track:delete_device_at(index)
    assert(index >= 2, "a track's mixer device stays")
    table.remove(self.devices, index)
  end
  return watched(track)
end

-- A new song; `file_name` is where it was loaded from or last saved ("" for none).
function fake.new_song(file_name)
  local song = {
    file_name = file_name or "",
    transport = watched({ bpm = 120, lpb = 4, tpl = 6, track_headroom = math.db2lin(-6) }),
    sequencer_track_count = 0,
    send_track_count = 0,
    tracks = {},
    instruments = { new_instrument() },
    patterns = { new_pattern() },
    sequencer = { pattern_sequence = { 1 }, sections = {} },
  }
  for n = 1, 8 do
    song.tracks[n] = new_track("Track 0" .. n, song)
  end
  song.tracks[9] = new_track("Master", song, 2)
  song.tracks[9]:insert_device_at("Audio/Effects/Native/Gainer", 2) -- as a template might
  song.sequencer_track_count = 8
  -- At or behind the master, a new track is a send track (the API's own
  -- words), and send tracks always come after the master.
  function song:insert_track_at(index)
    if index <= self.sequencer_track_count then
      table.insert(self.tracks, index, new_track("new", self))
      self.sequencer_track_count = self.sequencer_track_count + 1
    else
      assert(index <= #self.tracks + 1, "no such track position")
      local at = math.max(index, self.sequencer_track_count + 2)
      table.insert(self.tracks, at, new_track("send", self, 3))
      self.send_track_count = self.send_track_count + 1
    end
  end
  function song:delete_track_at(index)
    local master = self.sequencer_track_count + 1
    assert(index ~= master, "can't delete the master")
    if index < master then
      assert(self.sequencer_track_count > 1, "can't delete the last track")
      self.sequencer_track_count = self.sequencer_track_count - 1
    else
      self.send_track_count = self.send_track_count - 1
    end
    table.remove(self.tracks, index)
  end
  function song:track(index)
    return self.tracks[index]
  end
  function song:insert_instrument_at(index)
    table.insert(self.instruments, index, new_instrument())
  end
  function song:delete_instrument_at(index)
    table.remove(self.instruments, index)
  end
  function song:instrument(index)
    return self.instruments[index]
  end
  function song:pattern(index)
    return self.patterns[index]
  end
  local sequencer = song.sequencer
  function sequencer:insert_new_pattern_at(position)
    song.patterns[#song.patterns + 1] = new_pattern()
    table.insert(self.pattern_sequence, position, #song.patterns)
  end
  function sequencer:delete_sequence_at(position)
    table.remove(self.pattern_sequence, position)
  end
  -- Section starts and their names, by position in the sequence.
  function sequencer:set_sequence_is_start_of_section(position, is_section)
    assert(position >= 1 and position <= #self.pattern_sequence, "no such sequence slot")
    self.sections[position] = is_section and (self.sections[position] or "") or nil
  end
  function sequencer:set_sequence_section_name(position, name)
    assert(self.sections[position], "only a section's start has a name")
    self.sections[position] = name
  end
  function song:render(options, file, done)
    local active, on = {}, {}
    for index = 1, self.sequencer_track_count do
      if self.tracks[index].mute_state == 1 then
        active[#active + 1] = self.tracks[index].name
      end
    end
    for index, track in ipairs(self.tracks) do
      for k = 2, #track.devices do
        if track.devices[k].is_active then
          on[#on + 1] = index .. ":" .. (track.devices[k].display_name or track.devices[k].name)
        end
      end
    end
    fake.renders[#fake.renders + 1] =
      { file = file, active = active, rate = options.sample_rate, devices_on = on }
    local handle = assert(io.open(file, "w"))
    handle:write(table.concat(active, ","))
    handle:close()
    fake.pending_render = done
    return true
  end
  return song
end

-- The global `renoise` the tool sees.
function fake.install(bundle_path)
  local idle, new_document = {}, {}
  renoise = {
    RENOISE_VERSION = "3.5.4 (fake)",
    Sample = { INTERPOLATE_SINC = 4, NEW_NOTE_ACTION_NOTE_OFF = 2 },
    SampleModulationDevice = { TARGET_VOLUME = 1 },
    PatternTrackAutomation = { PLAYMODE_POINTS = 1, PLAYMODE_LINES = 2, PLAYMODE_CURVES = 3 },
    Track = { MUTE_STATE_ACTIVE = 1, MUTE_STATE_OFF = 2, MUTE_STATE_MUTED = 3 },
  }
  local state = {
    song = fake.new_song(),
    saved = {},
    prompts = {}, -- songs Renoise asked about saving
    unsaved = false, -- whether the open song has unsaved changes
    refuse_new_song = false,
  }
  function renoise.song()
    if state.loading then
      return nil -- Renoise is still loading a song
    end
    return state.song
  end
  local app = {}
  function app:new_song_no_template()
    state.new_song_wanted = true -- like Renoise: not now, but soon after this call
  end
  function app:save_song_as(path)
    local handle = assert(io.open(path, "w"))
    handle:write("xrns")
    handle:close()
    state.saved[#state.saved + 1] = path
    state.song.file_name = path
    state.saved_this_tick = true
  end
  function renoise.app()
    return app
  end
  local tool = {
    bundle_path = bundle_path,
    app_idle_observable = {
      add_notifier = function(_, f)
        idle[#idle + 1] = f
      end,
    },
    app_new_document_observable = {
      add_notifier = function(_, f)
        new_document[#new_document + 1] = f
      end,
    },
  }
  function renoise.tool()
    return tool
  end
  -- Renoise's additions to the standard library.
  function io.exists(path)
    local handle = io.open(path, "r")
    if handle then
      handle:close()
      return true
    end
    return os.rename(path, path) and true or false
  end
  function os.filenames(path, patterns)
    local names = {}
    local listing = io.popen('ls -1 "' .. path .. '" 2>/dev/null')
    for name in listing:lines() do
      for _, pattern in ipairs(patterns or { "*" }) do
        local lua_pattern = "^" .. pattern:gsub("%.", "%%."):gsub("%*", ".*") .. "$"
        if name:match(lua_pattern) then
          names[#names + 1] = name
        end
      end
    end
    listing:close()
    return names
  end

  local function arrive(song)
    state.song = song
    for _, f in ipairs(new_document) do
      f()
    end
  end

  -- Someone opens a song in this Renoise, and maybe changes it without saving.
  function state.open(path, changed)
    arrive(fake.new_song(path))
    state.unsaved = changed
  end

  -- Drives Renoise's event loop: new documents, finished renders, idle notifiers.
  function state.tick()
    local writes = fake.writes
    state.saved_this_tick = false
    if state.new_song_wanted then
      state.new_song_wanted = false
      if state.unsaved then
        state.prompts[#state.prompts + 1] = state.song.file_name
      elseif not state.refuse_new_song then
        arrive(fake.new_song())
      end
    end
    if fake.pending_render then
      local done = fake.pending_render
      fake.pending_render = nil
      done()
    end
    for _, f in ipairs(idle) do
      f()
    end
    if fake.writes > writes then
      state.unsaved = true -- recorded once the tick is over, after any save in it
    elseif state.saved_this_tick then
      state.unsaved = false
    end
  end
  return state
end

-- Renoise's additions to Lua's math library, which a new song already uses.
function math.db2lin(value)
  return 10 ^ (value / 20)
end

function math.lin2db(value)
  return 20 * math.log10(value)
end

return fake
