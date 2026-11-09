-- mph bridge (spec/components/renoise.md, REQ-REN-03).
--
-- Watches mph's job folder. A job (`<name>.job.lua`) is a Lua data table that
-- mph wrote: a list of songs, each with its plan, where to save it, and the
-- renders wanted. For each song this tool starts a new song, builds it
-- (build.lua), saves it, renders each WAV, and finally writes
-- `<name>.result.json`, with the error if anything failed. Heavy work runs
-- from the idle loop, never inside a notifier.

local build = require("build")
local json = require("json")

local NEW_SONG_TIMEOUT_S = 120
local POLL_EVERY_S = 1

local job = nil -- the job in progress
local last_poll = 0

local function join(folder, name)
  if folder:sub(-1) == "/" or folder:sub(-1) == "\\" then
    return folder .. name
  end
  return folder .. "/" .. name
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
  write_atomically(job.base .. ".result.json", json.encode(result) .. "\n")
  os.remove(job.base .. ".working")
  job = nil
end

local function fail(problem)
  finish({ ok = false, error = tostring(problem) })
end

local function current_song()
  return job.data.songs[job.song]
end

local function next_song()
  job.song = job.song + 1
  if job.song > #job.data.songs then
    finish({ ok = true })
    return
  end
  job.phase = "new_song"
  job.since = os.time()
  renoise.app():new_song_no_template()
end

local function next_render()
  local plan = current_song()
  local song = renoise.song()
  job.render = job.render + 1
  local wanted = plan.renders[job.render]
  if wanted == nil then
    build.solo(song, nil)
    renoise.app():save_song_as(plan.save) -- saved with nothing muted
    next_song()
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
  job = { base = base, song = 0, files = {} }
  local data, problem = load_data(working)
  if type(data) ~= "table" or type(data.songs) ~= "table" then
    fail("unreadable job: " .. tostring(problem))
    return
  end
  job.data = data
  next_song()
end

local function step(action)
  local ok, problem = pcall(action)
  if not ok and job then
    fail(problem)
  end
end

local function on_idle()
  if job then
    if job.phase == "build" then
      job.phase = "building"
      step(build_song)
    elseif job.phase == "rendered" then
      step(next_render)
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
  if job and job.phase == "new_song" then
    job.phase = "build"
  end
end

renoise.tool().app_idle_observable:add_notifier(on_idle)
renoise.tool().app_new_document_observable:add_notifier(on_new_document)
