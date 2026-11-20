-- Builds one song from a plan table (spec/components/renoise.md: REQ-REN-01, REQ-REN-06,
-- REQ-REN-09 to REQ-REN-12; spec/components/tracks.md: REQ-TRK-02, REQ-TRK-04).
-- Pure use of the Renoise API, so tests can run it against a fake `renoise`.
--
-- A song plan:
--   bpm, lpb, lines, tpl, headroom_db, instruments (how many)
--   patterns = { { lines, section = name or nil }, ... }: a section starts a pattern
--   tracks = { { name, sample, volume, transpose, fine_tune, slices = {frame, ...},
--                instrument, clips = { { file, name }, ... } or nil,
--                zones = { { file, note, low, high }, ... } or nil (a sampled synth),
--                accents = zones or nil, accent_instrument, release (seconds),
--                notes = { { line, delay, volume, note | slice | clip | off,
--                            accent, glide }, ... } }, ... }
--   mix, modes: the mixer (mixer.lua), if the score has one
-- Lines are 0-based across the whole song; Renoise's own indices are 1-based.

local mixer = require("mixer")

local M = {}

M.FILTER = "Audio/Effects/Native/Digital Filter"
M.AHDSR = "Modulation/AHDSR"
M.NOTE_OFF = 120
M.C4 = 48
M.GLIDE = "0G"
M.NAMES = { "C-", "C#", "D-", "D#", "E-", "F-", "F#", "G-", "G#", "A-", "A#", "B-" }

-- A Renoise note as it shows in a pattern: 48 is C-4.
function M.note_name(note)
  return M.NAMES[note % 12 + 1] .. math.floor(note / 12)
end

-- A track inserted at or behind the master is a send track, so new sequencer
-- tracks go in at the front; every track is set up afresh anyway.
local function ensure_tracks(song, count)
  while song.sequencer_track_count < count do
    song:insert_track_at(1)
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

-- A track's columns, and with no mix, the bypassed filter of REQ-REN-06 (a
-- mix plans its own devices, that filter among them where it's wanted).
local function setup_track(track, plan, spare_filter)
  track.name = plan.name
  track.visible_note_columns = 1
  track.volume_column_visible = true
  track.delay_column_visible = true
  if plan.zones then
    track.visible_effect_columns = 1 -- where glides go
  end
  if not spare_filter then
    return
  end
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

-- A volume envelope that holds a note until its note-off, then releases it
-- over the synth's own release time (REQ-REN-09). Its parameters go in
-- `described`, like the mixer's devices.
local function release_envelope(instrument, release_s, described)
  if #instrument.sample_modulation_sets == 0 then
    instrument:insert_sample_modulation_set_at(1)
  end
  local set = instrument.sample_modulation_sets[1]
  set:init()
  set.name = "Gate"
  local volume = renoise.SampleModulationDevice.TARGET_VOLUME
  local ok, envelope = pcall(set.insert_device_at, set, M.AHDSR, volume, 1)
  if not ok or envelope == nil then
    error(string.format("%s: Renoise has no modulation device %s (it has %s)", instrument.name,
      M.AHDSR, mixer.near(set.available_devices, M.AHDSR)), 0)
  end
  if described[M.AHDSR] == nil then
    described[M.AHDSR] = mixer.describe(envelope)
  end
  if envelope.tempo_sync_switching_allowed then
    envelope.tempo_synced = false -- times in ms
  end
  envelope.attack.value = envelope.attack.value_min
  envelope.hold.value = envelope.hold.value_min
  envelope.sustain.value = envelope.sustain.value_max
  local release = mixer.value_for(M.AHDSR, envelope.release, "ms", release_s * 1000)
  if release == nil then
    error(string.format("%s: the AHDSR's %s never shows %g ms", instrument.name,
      envelope.release.name, release_s * 1000), 0)
  end
  envelope.release.value = release
end

-- A sampled synth (REQ-REN-09): one sample per sound, each on its own key and
-- the keys nearest it, and a new note releasing the one before.
local function setup_synth(instrument, plan, zones, name, described)
  instrument.name = name
  while #instrument.samples < #zones do
    instrument:insert_sample_at(#instrument.samples + 1)
  end
  while #instrument.samples > #zones do
    instrument:delete_sample_at(#instrument.samples)
  end
  release_envelope(instrument, plan.release or 0, described)
  for k, zone in ipairs(zones) do
    local sample = instrument.samples[k]
    if not sample.sample_buffer:load_from(zone.file) then
      error("Renoise could not load " .. zone.file)
    end
    sample.name = name .. " " .. M.note_name(zone.note)
    tune(sample, plan)
    sample.new_note_action = renoise.Sample.NEW_NOTE_ACTION_NOTE_OFF
    sample.modulation_set_index = 1
    sample.sample_mapping.base_note = zone.note
    sample.sample_mapping.note_range = { zone.low, zone.high }
  end
end

-- Sets up a track's instrument(s) and returns how its notes map to Renoise notes.
local function setup_sound(song, plan, index, described)
  local instrument = song:instrument(plan.instrument or index)
  if plan.clips then
    return setup_clips(instrument, plan)
  elseif plan.zones then
    setup_synth(instrument, plan, plan.zones, plan.name, described)
    if plan.accents then
      local accents = song:instrument(plan.accent_instrument)
      setup_synth(accents, plan, plan.accents, plan.name .. " accent", described)
    end
    return function(note)
      return note.note
    end
  end
  return setup_instrument(instrument, plan)
end

local function write_note(song, starts, index, plan, note, pitch)
  local position = #starts
  while starts[position] > note.line do
    position = position - 1
  end
  local pattern = song:pattern(song.sequencer.pattern_sequence[position])
  local line = pattern:track(index):line(note.line - starts[position] + 1)
  local column = line:note_column(1)
  if note.off then
    column.note_value = M.NOTE_OFF
  elseif note.glide then -- tied into: the playing note bends to this one
    column.note_value = pitch
    local effect = line:effect_column(1)
    effect.number_string = M.GLIDE
    effect.amount_value = note.glide
  else
    local instrument = (note.accent and plan.accent_instrument) or plan.instrument or index
    column.note_value = pitch
    column.instrument_value = instrument - 1
    column.volume_value = note.volume
  end
  column.delay_value = note.delay
end

-- Builds the song. Each kind of mixer device's parameters go in `described`;
-- anything Renoise doesn't have, or can't show, fails the build with all of
-- it named.
function M.build(song, plan, described)
  described = described or {}
  mixer.reset()
  song.transport.bpm = plan.bpm
  song.transport.lpb = plan.lpb
  if plan.tpl then
    song.transport.tpl = plan.tpl
  end
  if plan.headroom_db then
    song.transport.track_headroom = math.db2lin(plan.headroom_db) -- REQ-REN-12
  end
  ensure_tracks(song, #plan.tracks)
  mixer.ensure_sends(song, plan.mix and plan.mix.sends or {})
  ensure_instruments(song, plan.instruments or #plan.tracks)
  local starts = ensure_patterns(song, plan)
  for index, track_plan in ipairs(plan.tracks) do
    setup_track(song:track(index), track_plan, plan.mix == nil)
    local pitch_of = setup_sound(song, track_plan, index, described)
    for _, note in ipairs(track_plan.notes) do
      write_note(song, starts, index, track_plan, note, (not note.off) and pitch_of(note) or nil)
    end
  end
  local problems = {}
  if plan.mix then
    mixer.build(song, plan, described, problems)
  end
  if #problems > 0 then
    error(table.concat(problems, "\n"), 0) -- level 0: the problems alone, for Dean to read
  end
end

-- Turns the mixer's devices on or off for a kind of render (mixer.lua).
M.switch = mixer.switch

-- What the song's mixer devices (mixer.lua) and synth envelopes show once
-- built, for the result file; nil for a song with neither.
function M.shown(song, plan)
  local envelopes = {}
  for index, track in ipairs(plan.tracks) do
    for _, k in ipairs(track.zones and { track.instrument or index, track.accent_instrument } or {}) do
      local instrument = song:instrument(k)
      local set = instrument.sample_modulation_sets[1]
      local envelope = set and set.devices[1]
      if envelope then
        envelopes[#envelopes + 1] = { instrument = instrument.name, release = envelope.release.value_string }
      end
    end
  end
  if plan.mix == nil and #envelopes == 0 then
    return nil
  end
  return {
    tracks = plan.mix and mixer.shown(song, plan) or {},
    envelopes = envelopes,
    refused_ends = mixer.refused(), -- automations kept to their pattern's last line
  }
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
