-- Runs the bridge tool against fake_renoise.lua and prints what happened as JSON.
--   lua5.1 harness.lua <tool dir> build <plan.lua>
--   lua5.1 harness.lua <tool dir> job <installed tool dir> <ticks> [option ...]
--   lua5.1 harness.lua <tool dir> data <file.lua>
-- Job options: `refuse` (Renoise never starts a new song), `open=<path>`
-- (someone has that song open before the first tick), `changed` (with
-- unsaved changes) and `loading=<n>` (no song for the first n ticks, as while
-- one loads). Each tick moves the clock on 1000 s, so timeouts are quick.

local here = arg[0]:match("^(.*)/") or "."
local tool_dir, mode = arg[1], arg[2]
package.path = tool_dir .. "/?.lua;" .. here .. "/?.lua;" .. package.path

local fake = require("fake_renoise")
local json = require("json")

local function load_data(path)
  local chunk = assert(loadfile(path))
  setfenv(chunk, {})
  return chunk()
end

local function describe(song)
  local out = { bpm = song.transport.bpm, lpb = song.transport.lpb, tracks = {}, notes = {} }
  out.patterns = {}
  out.sections = {}
  local offset = 0 -- lines in the patterns before this one
  for position, index in ipairs(song.sequencer.pattern_sequence) do
    local pattern = song.patterns[index]
    out.patterns[position] = pattern.number_of_lines
    local section = song.sequencer.sections[position]
    if section then
      out.sections[#out.sections + 1] = { position = position, line = offset, name = section }
    end
    for track_index, track in pairs(pattern.tracks) do
      for line_index, line in pairs(track.lines) do
        local column = line.columns[1]
        if column then
          out.notes[#out.notes + 1] = {
            track = track_index,
            line = offset + line_index - 1,
            note = column.note_value,
            instrument = column.instrument_value,
            volume = column.volume_value,
            delay = column.delay_value,
          }
        end
      end
    end
    offset = offset + pattern.number_of_lines
  end
  table.sort(out.notes, function(a, b)
    if a.track ~= b.track then
      return a.track < b.track
    end
    return a.line < b.line
  end)
  for index = 1, song.sequencer_track_count do
    local track = song.tracks[index]
    local instrument = song.instruments[index]
    local sample = instrument.samples[1] or { sample_mapping = {} } -- a clip track may load none
    local devices = {}
    for _, device in ipairs(track.devices) do
      devices[#devices + 1] = { name = device.name, active = device.is_active }
    end
    local zones = {}
    for _, each in ipairs(instrument.samples) do
      local mapping = each.sample_mapping
      if mapping.note_range[1] == mapping.note_range[2] then -- a sample on a key of its own
        zones[#zones + 1] = {
          name = each.name,
          sample = each.loaded,
          base_note = mapping.base_note,
          note_range = mapping.note_range,
          volume = each.volume,
          autoseek = each.autoseek,
          autofade = each.autofade,
        }
      end
    end
    out.tracks[index] = {
      zones = zones,
      name = track.name,
      devices = devices,
      volume_column = track.volume_column_visible,
      delay_column = track.delay_column_visible,
      instrument = instrument.name,
      sample = sample.loaded,
      volume = sample.volume,
      transpose = sample.transpose,
      fine_tune = sample.fine_tune,
      interpolation = sample.interpolation_mode,
      slices = sample.slice_markers,
      samples = #instrument.samples,
    }
  end
  out.instruments = #song.instruments
  return out
end

if mode == "data" then
  print(json.encode(load_data(arg[3])))
elseif mode == "build" then
  fake.install(tool_dir)
  local build = require("build")
  local song = renoise.song()
  build.build(song, load_data(arg[3]))
  print(json.encode(describe(song)))
elseif mode == "job" then
  local state = fake.install(arg[3] .. "/")
  local options = {}
  for n = 5, #arg do
    local key, value = arg[n]:match("^([^=]+)=(.*)$")
    options[key or arg[n]] = value or true
  end
  state.refuse_new_song = options.refuse == true
  state.clock = 0
  os.time = function()
    return state.clock
  end
  dofile(tool_dir .. "/main.lua")
  if options.open then
    state.open(options.open, options.changed == true)
  end
  for n = 1, tonumber(arg[4]) do
    state.clock = n * 1000
    state.loading = n <= (tonumber(options.loading) or 0)
    state.tick()
  end
  print(json.encode({
    saved = state.saved,
    renders = fake.renders,
    prompts = state.prompts,
    unsaved = state.unsaved,
    open = state.song.file_name,
  }))
end
