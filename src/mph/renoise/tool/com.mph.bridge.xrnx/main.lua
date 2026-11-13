-- mph bridge (spec/components/renoise.md, REQ-REN-03, REQ-REN-08).
--
-- Watches mph's job folder. A job (`<name>.job.lua`) is a Lua data table that
-- mph wrote: a list of songs, each with its plan, where to save it, and the
-- renders wanted, and where scratch songs go. For a job this tool first saves
-- the open song if it's mph's. Then, for each song, it starts a new song,
-- builds it (build.lua), saves it, renders each WAV, and saves it again. Last
-- it saves the open song as mph's scratch song and writes
-- `<name>.result.json`, with the error if anything failed. Heavy work runs
-- from the idle loop, never inside a notifier.
--
-- Nothing is left unsaved (REQ-REN-08). Renoise can count a step's changes as
-- unsaved even when the step saved the song, so a save that must leave the
-- song with nothing to ask about runs in an idle step with no changes in it.

local build = require("build")
local json = require("json")

local NEW_SONG_TIMEOUT_S = 120
local POLL_EVERY_S = 1

local job = nil -- the job in progress
local last_poll = 0
local own_song = false -- whether the open song is one this tool started

local function join(folder, name)
  if folder:sub(-1) == "/" or folder:sub(-1) == "\\" then
    return folder .. name
  end
  return folder .. "/" .. name
end

-- Whether a song's file is somewhere inside a folder.
local function inside(path, folder)
  local prefix = join(folder, "")
  return path:sub(1, #prefix) == prefix
end

-- A song file's name, without its folder or `.xrns`.
local function stem(path)
  local name = path:match("[^/\\]+$") or path
  return (name:gsub("%.[xX][rR][nN][sS]$", ""))
end

-- Reads a Lua data file with no access to anything (it holds only a table).
local function load_data(path)
  local chunk, problem = loadfile(path)
  if not chunk then
    return nil, problem
  end
  setfenv(chunk, {})
  local ok, value = pcall(chunk)
  if not ok then
    return nil, value
  end
  return value
end

local function jobs_folder()
  local settings = load_data(join(renoise.tool().bundle_path, "settings.lua"))
  return settings and settings.jobs
end

local function write_atomically(path, text)
  local temporary = path .. ".tmp"
  local file = assert(io.open(temporary, "w"))
  file:write(text)
  file:close()
  os.remove(path)
  assert(os.rename(temporary, path))
end

local function finish(result)
  result.renoise = renoise.RENOISE_VERSION
  result.files = job.files
  result.kept = job.kept
  write_atomically(job.base .. ".result.json", json.encode(result) .. "\n")
  os.remove(job.base .. ".working")
  job = nil
end

-- A failure is reported on the next idle step, so the scratch save before it
-- runs in a step with no changes.
local function fail(problem)
  job.phase = "failed"
  job.error = tostring(problem)
end

local function report_failure()
  if own_song and job.data then
    pcall(function()
      renoise.app():save_song_as(job.data.scratch.song)
    end)
  end
  finish({ ok = false, error = job.error })
end

local function current_song()
  return job.data.songs[job.song]
end

local function next_song()
  job.song = job.song + 1
  if job.song > #job.data.songs then
    if own_song then
      renoise.app():save_song_as(job.data.scratch.song) -- what's left open stays on disk
    end
    finish({ ok = true })
    return
  end
  job.phase = "new_song"
  job.since = os.time()
  renoise.app():new_song_no_template()
end

-- Saves the open song if it's mph's, so starting a new song never asks to
-- save it. A song that isn't mph's is left for Renoise to ask about.
local function save_open_song()
  local current = renoise.song()
  if current == nil then
    job.phase = "open" -- a song is still loading: try again on the next step
    return
  end
  local scratch = job.data.scratch
  local open = current.file_name
  if own_song then
    renoise.app():save_song_as(scratch.song)
  elseif open ~= "" and inside(open, scratch.ours) then
    local when = os.date("!%Y%m%d-%H%M%S", os.time())
    local copy = join(scratch.folder, when .. " " .. stem(open) .. ".xrns")
    renoise.app():save_song_as(copy)
    job.kept[#job.kept + 1] = { from = open, to = copy }
  end
  next_song()
end

local function next_render()
  local plan = current_song()
  local song = renoise.song()
  job.render = job.render + 1
  local wanted = plan.renders[job.render]
  if wanted == nil then
    build.solo(song, nil)
    job.phase = "save" -- on the next idle step, after this change
    return
  end
  build.solo(song, wanted.solo)
  job.phase = "rendering"
  local options = {
    sample_rate = plan.rate,
    bit_depth = 32,
    interpolation = "precise",
    priority = "high",
  }
  local ok, problem = song:render(options, wanted.file, function()
    job.files[#job.files + 1] = wanted.file
    job.phase = "rendered"
  end)
  if not ok then
    error("render failed: " .. tostring(problem))
  end
end

local function save_song()
  renoise.app():save_song_as(current_song().save) -- saved with nothing muted
  next_song()
end

local function build_song()
  local plan = current_song()
  build.build(renoise.song(), plan)
  renoise.app():save_song_as(plan.save)
  job.render = 0
  next_render()
end

local function start(folder, name)
  local base = join(folder, name:sub(1, -#".job.lua" - 1))
  local working = base .. ".working"
  if not os.rename(join(folder, name), working) then
    return
  end
  job = { base = base, song = 0, files = {}, kept = {} }
  local data, problem = load_data(working)
  if type(data) ~= "table" or type(data.songs) ~= "table" or type(data.scratch) ~= "table" then
    fail("unreadable job: " .. tostring(problem))
    return
  end
  job.data = data
  job.phase = "open"
end

-- What an idle step does, by the phase the job is in. Each sets the next phase.
local STEPS = {
  open = save_open_song,
  build = build_song,
  rendered = next_render,
  save = save_song,
}

local function step(action)
  local ok, problem = pcall(action)
  if not ok and job then
    fail(problem)
  end
end

local function on_idle()
  if job then
    local action = STEPS[job.phase]
    if action then
      job.phase = "busy"
      step(action)
    elseif job.phase == "failed" then
      report_failure()
    elseif job.phase == "new_song" and os.time() - job.since > NEW_SONG_TIMEOUT_S then
      fail("Renoise didn't start a new song. Save or close the open song, then render again.")
    end
    return
  end
  if os.time() - last_poll < POLL_EVERY_S then
    return
  end
  last_poll = os.time()
  local folder = jobs_folder()
  if not folder or not io.exists(folder) then
    return
  end
  local names = os.filenames(folder, { "*.job.lua" })
  table.sort(names)
  if names[1] then
    step(function()
      start(folder, names[1])
    end)
  end
end

local function on_new_document()
  own_song = job ~= nil and job.phase == "new_song"
  if own_song then
    job.phase = "build"
  end
end

renoise.tool().app_idle_observable:add_notifier(on_idle)
renoise.tool().app_new_document_observable:add_notifier(on_new_document)
