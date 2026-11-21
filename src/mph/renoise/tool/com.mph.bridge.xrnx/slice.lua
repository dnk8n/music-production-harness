-- Time slices for long work (spec/components/renoise.md, REQ-REN-03).
--
-- Renoise asks whether to stop a script that "may be busy" when one call
-- into it runs too long, and the bridge then waits for someone to click. So
-- the build runs in a coroutine (main.lua) and calls `pause` often: once
-- about `SLICE_S` of CPU time has passed since the slice began, it yields,
-- and the idle loop resumes it on its next step. Outside a coroutine (the
-- tests' direct calls) it does nothing.

local M = {}

M.SLICE_S = 0.2

local began = os.clock()

local function in_coroutine()
  local running, main = coroutine.running() -- Lua 5.1: nil on the main thread
  return running ~= nil and not main
end

-- Starts a slice: the idle step's own clock.
function M.begin()
  began = os.clock()
end

-- Yields if this slice has used its time. Never call it inside a pcall.
function M.pause()
  if in_coroutine() and os.clock() - began >= M.SLICE_S then
    coroutine.yield()
    began = os.clock()
  end
end

return M
