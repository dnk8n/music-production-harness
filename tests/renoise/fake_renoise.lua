-- A fake of the parts of the Renoise Lua API the bridge tool uses, for tests.
-- It records state so a test can print the song the tool built.

local fake = { renders = {} }

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
    sample_mapping = { base_note = 48 },
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
  return track
end

function fake.new_song()
  local song = {
    transport = { bpm = 120, lpb = 4 },
    sequencer_track_count = 0,
    tracks = {},
    instruments = { new_instrument() },
    patterns = { new_pattern() },
    sequencer = { pattern_sequence = { 1 } },
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
  local state = { song = fake.new_song(), saved = {}, refuse_new_song = false }
  renoise = {
    RENOISE_VERSION = "3.5.4 (fake)",
    Sample = { INTERPOLATE_SINC = 4 },
    Track = { MUTE_STATE_ACTIVE = 1, MUTE_STATE_OFF = 2, MUTE_STATE_MUTED = 3 },
  }
  function renoise.song()
    return state.song
  end
  local app = {}
  function app:new_song_no_template()
    if not state.refuse_new_song then
      state.next_song = fake.new_song()
    end
  end
  function app:save_song_as(path)
    local handle = assert(io.open(path, "w"))
    handle:write("xrns")
    handle:close()
    state.saved[#state.saved + 1] = path
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

  -- Drives Renoise's event loop: idle ticks, new documents and finished renders.
  function state.tick()
    if state.next_song then
      state.song, state.next_song = state.next_song, nil
      for _, f in ipairs(new_document) do
        f()
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
  end
  return state
end

return fake
