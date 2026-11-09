-- Builds one song from a plan table (spec/components/renoise.md: REQ-REN-01, REQ-REN-06).
-- Pure use of the Renoise API, so tests can run it against a fake `renoise`.
--
-- A song plan:
--   bpm, lpb, pattern_lines, lines
--   tracks = { { name, sample, volume, transpose, fine_tune, slices = {frame, ...},
--                notes = { { line, delay, volume, note | slice | off }, ... } }, ... }
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

local function ensure_patterns(song, plan)
  local count = math.max(1, math.ceil(plan.lines / plan.pattern_lines))
  local sequencer = song.sequencer
  while #sequencer.pattern_sequence < count do
    sequencer:insert_new_pattern_at(#sequencer.pattern_sequence + 1)
  end
  while #sequencer.pattern_sequence > count do
    sequencer:delete_sequence_at(#sequencer.pattern_sequence)
  end
  for position = 1, count do
    local pattern = song:pattern(sequencer.pattern_sequence[position])
    pattern:clear()
    local left = plan.lines - (position - 1) * plan.pattern_lines
    pattern.number_of_lines = math.min(plan.pattern_lines, left)
  end
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

local function write_note(song, plan, track_index, instrument_index, note, pitch)
  local position = math.floor(note.line / plan.pattern_lines) + 1
  local line = note.line % plan.pattern_lines + 1
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
  ensure_patterns(song, plan)
  for index, track_plan in ipairs(plan.tracks) do
    setup_track(song:track(index), track_plan)
    local pitch_of = setup_instrument(song:instrument(index), track_plan)
    for _, note in ipairs(track_plan.notes) do
      write_note(song, plan, index, index, note, (not note.off) and pitch_of(note) or nil)
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
