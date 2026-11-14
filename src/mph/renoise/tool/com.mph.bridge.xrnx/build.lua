-- Builds one song from a plan table (spec/components/renoise.md: REQ-REN-01, REQ-REN-06;
-- spec/components/tracks.md: REQ-TRK-02, REQ-TRK-04).
-- Pure use of the Renoise API, so tests can run it against a fake `renoise`.
--
-- A song plan:
--   bpm, lpb, lines
--   patterns = { { lines, section = name or nil }, ... }: a section starts a pattern
--   tracks = { { name, sample, volume, transpose, fine_tune, slices = {frame, ...},
--                clips = { { file, name }, ... } or nil,
--                notes = { { line, delay, volume, note | slice | clip | off }, ... } }, ... }
-- Lines are 0-based across the whole song; Renoise's own indices are 1-based.

local M = {}

M.FILTER = "Audio/Effects/Native/Digital Filter"
M.NOTE_OFF = 120
M.C4 = 48

local function ensure_tracks(song, count)
  while song.sequencer_track_count < count do
    song:insert_track_at(song.sequencer_track_count + 1)
  end
  while song.sequencer_track_count > count do
    song:delete_track_at(song.sequencer_track_count)
  end
end

local function ensure_instruments(song, count)
  while #song.instruments < count do
    song:insert_instrument_at(#song.instruments + 1)
  end
  while #song.instruments > count do
    song:delete_instrument_at(#song.instruments)
  end
end

-- Makes the song's patterns, names its sections, and returns each pattern's first line.
local function ensure_patterns(song, plan)
  local count = #plan.patterns
  local sequencer = song.sequencer
  while #sequencer.pattern_sequence < count do
    sequencer:insert_new_pattern_at(#sequencer.pattern_sequence + 1)
  end
  while #sequencer.pattern_sequence > count do
    sequencer:delete_sequence_at(#sequencer.pattern_sequence)
  end
  local starts, line = {}, 0
  for position, wanted in ipairs(plan.patterns) do
    local pattern = song:pattern(sequencer.pattern_sequence[position])
    pattern:clear()
    pattern.number_of_lines = wanted.lines
    if wanted.section then
      sequencer:set_sequence_is_start_of_section(position, true)
      sequencer:set_sequence_section_name(position, wanted.section)
    end
    starts[position] = line
    line = line + wanted.lines
  end
  return starts
end

local function setup_track(track, plan)
  track.name = plan.name
  track.visible_note_columns = 1
  track.volume_column_visible = true
  track.delay_column_visible = true
  local ok, device = pcall(track.insert_device_at, track, M.FILTER, #track.devices + 1)
  if ok and device then
    device.is_active = false -- ready to use, but bypassed so the render is unchanged
  end
end

local function tune(sample, plan)
  sample.volume = plan.volume
  sample.transpose = plan.transpose or 0
  sample.fine_tune = plan.fine_tune or 0
  sample.interpolation_mode = renoise.Sample.INTERPOLATE_SINC
end

-- Loads the sound and returns a function mapping a note plan to a Renoise note.
local function setup_instrument(instrument, plan)
  instrument.name = plan.name
  if #instrument.samples == 0 then
    instrument:insert_sample_at(1)
  end
  local sample = instrument.samples[1]
  if not sample.sample_buffer:load_from(plan.sample) then
    error("Renoise could not load " .. plan.sample)
  end
  sample.name = plan.name
  tune(sample, plan)
  for _, frame in ipairs(plan.slices or {}) do
    sample:insert_slice_marker(frame + 1)
  end
  local notes = {}
  for k = 1, #(plan.slices or {}) do
    local alias = instrument.samples[k + 1]
    notes[k] = alias.sample_mapping.base_note
    pcall(tune, alias, plan) -- slices may inherit these; set them anyway where allowed
  end
  return function(note)
    if note.slice then
      return notes[note.slice] or error(plan.name .. " has no slice " .. note.slice)
    end
    return note.note or M.C4
  end
end

-- One sample per clip, each on its own key from C-4 up, at its own pitch. Autoseek
-- plays a clip from the right place when playback starts in its middle, and
-- autofade keeps its cuts free of clicks.
local function setup_clips(instrument, plan)
  instrument.name = plan.name
  while #instrument.samples < #plan.clips do
    instrument:insert_sample_at(#instrument.samples + 1)
  end
  for k, clip in ipairs(plan.clips) do
    local sample = instrument.samples[k]
    if not sample.sample_buffer:load_from(clip.file) then
      error("Renoise could not load " .. clip.file)
    end
    sample.name = clip.name
    tune(sample, plan)
    sample.autoseek = true
    sample.autofade = true
    local key = M.C4 + k - 1
    sample.sample_mapping.base_note = key
    sample.sample_mapping.note_range = { key, key }
  end
  return function(note)
    return M.C4 + note.clip - 1
  end
end

local function write_note(song, starts, track_index, instrument_index, note, pitch)
  local position = #starts
  while starts[position] > note.line do
    position = position - 1
  end
  local line = note.line - starts[position] + 1
  local pattern = song:pattern(song.sequencer.pattern_sequence[position])
  local column = pattern:track(track_index):line(line):note_column(1)
  if note.off then
    column.note_value = M.NOTE_OFF
  else
    column.note_value = pitch
    column.instrument_value = instrument_index - 1
    column.volume_value = note.volume
  end
  column.delay_value = note.delay
end

function M.build(song, plan)
  song.transport.bpm = plan.bpm
  song.transport.lpb = plan.lpb
  ensure_tracks(song, #plan.tracks)
  ensure_instruments(song, #plan.tracks)
  local starts = ensure_patterns(song, plan)
  for index, track_plan in ipairs(plan.tracks) do
    setup_track(song:track(index), track_plan)
    local setup = track_plan.clips and setup_clips or setup_instrument
    local pitch_of = setup(song:instrument(index), track_plan)
    for _, note in ipairs(track_plan.notes) do
      write_note(song, starts, index, index, note, (not note.off) and pitch_of(note) or nil)
    end
  end
end

-- Mutes every sequencer track except `solo` (nil: none muted).
function M.solo(song, solo)
  for index = 1, song.sequencer_track_count do
    local track = song:track(index)
    if solo == nil or index == solo then
      track.mute_state = renoise.Track.MUTE_STATE_ACTIVE
    else
      track.mute_state = renoise.Track.MUTE_STATE_MUTED
    end
  end
end

return M
