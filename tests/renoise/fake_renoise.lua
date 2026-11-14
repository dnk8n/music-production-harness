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

local function new_column()
  return { note_value = 121, instrument_value = 255, volume_value = 255, delay_value = 0 }
end

local function new_pattern()
  local pattern = { number_of_lines = 64, tracks = {} }
  function pattern:clear()
    self.tracks = {}
  end
  function pattern:track(index)
    self.tracks[index] = self.tracks[index] or { lines = {} }
    local track = self.tracks[index]
    function track:line(line_index)
      assert(line_index >= 1 and line_index <= pattern.number_of_lines, "line out of range")
      self.lines[line_index] = self.lines[line_index] or { columns = {} }
      local line = self.lines[line_index]
      function line:note_column(column)
        self.columns[column] = self.columns[column] or new_column()
        return self.columns[column]
      end
      return line
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

local function new_instrument()
  local instrument = { name = "", samples = {} }
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
  return instrument
end

local function new_track(name)
  local track = {
    name = name,
    mute_state = 1,
    devices = { { name = "TrackVolPan", is_active = true } },
    visible_note_columns = 1,
    volume_column_visible = false,
    delay_column_visible = false,
  }
  function track:insert_device_at(path, index)
    local device = { name = path, is_active = true }
    table.insert(self.devices, index, device)
    return device
  end
  return watched(track)
end

-- A new song; `file_name` is where it was loaded from or last saved ("" for none).
function fake.new_song(file_name)
  local song = {
    file_name = file_name or "",
    transport = watched({ bpm = 120, lpb = 4 }),
    sequencer_track_count = 0,
    tracks = {},
    instruments = { new_instrument() },
    patterns = { new_pattern() },
    sequencer = { pattern_sequence = { 1 }, sections = {} },
  }
  for n = 1, 8 do
    song.tracks[n] = new_track("Track 0" .. n)
  end
  song.sequencer_track_count = 8
  function song:insert_track_at(index)
    table.insert(self.tracks, index, new_track("new"))
    self.sequencer_track_count = self.sequencer_track_count + 1
  end
  function song:delete_track_at(index)
    assert(self.sequencer_track_count > 1, "can't delete the last track")
    table.remove(self.tracks, index)
    self.sequencer_track_count = self.sequencer_track_count - 1
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
    local active = {}
    for index = 1, self.sequencer_track_count do
      if self.tracks[index].mute_state == 1 then
        active[#active + 1] = self.tracks[index].name
      end
    end
    fake.renders[#fake.renders + 1] = { file = file, active = active, rate = options.sample_rate }
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
  local state = {
    song = fake.new_song(),
    saved = {},
    prompts = {}, -- songs Renoise asked about saving
    unsaved = false, -- whether the open song has unsaved changes
    refuse_new_song = false,
  }
  renoise = {
    RENOISE_VERSION = "3.5.4 (fake)",
    Sample = { INTERPOLATE_SINC = 4 },
    Track = { MUTE_STATE_ACTIVE = 1, MUTE_STATE_OFF = 2, MUTE_STATE_MUTED = 3 },
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

return fake
