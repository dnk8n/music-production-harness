// SynthDefs for benchmark renders (spec/components/benchmarks.md, ADR-0002).
// Run as: sclang synthdefs.scd <output folder>
// mph compiles this once per change and caches the .scsyndef files in data_dir.
//
// Output goes through OffsetOut so hits land on their exact sample, not the
// start of a 64-sample block.
// Every voice: out (first of two channels), amp, gate (0 chokes it), seed.
// Noise is seeded per hit: each synth takes its own random generator (rid, one
// of scsynth's 64) and seeds it, so every hit of a voice sounds the same and
// renders are bit-identical (REQ-AUD-05). Sharing a generator would make the
// noise depend on which other synths happen to be playing.
(
var dir = thisProcess.argv[0];
var write = { |def| def.writeDefFile(dir) };

// Sample playback. start in frames, dur in seconds (0 = to the end of the
// buffer), attack/release in seconds. Slices use a 1 ms attack and release.
[1, 2].do { |channels|
    write.(SynthDef(("mph_play" ++ channels).asSymbol, {
        |out = 0, buf = 0, amp = 1, rate = 1, start = 0, dur = 0, attack = 0,
         release = 0.005, gate = 1|
        var length = Select.kr(dur > 0, [BufDur.ir(buf) / rate, dur]);
        var fadeIn = EnvGen.ar(Env([attack <= 0, 1], [attack]));
        var cut = EnvGen.ar(Env([1, 1, 0], [length, release]), doneAction: 2);
        var choke = EnvGen.ar(Env([1, 1, 0], [0, release], releaseNode: 1), gate,
            doneAction: 2);
        var sig = PlayBuf.ar(channels, buf, BufRateScale.ir(buf) * rate, startPos: start,
            doneAction: 2);
        sig = sig * (amp * fadeIn * cut * choke);
        OffsetOut.ar(out, if(channels == 1) { sig ! 2 } { sig });
    }));
};

// Synth drums, roughly 808-style. Mono, written to both channels.
write.(SynthDef(\mph_kick, { |out = 0, amp = 1, gate = 1, seed = 1, rid = 0, decay = 0.45|
    var pitch = EnvGen.ar(Env([160, 52, 45], [0.04, 0.3], \exp));
    var body = SinOsc.ar(pitch) * EnvGen.ar(Env.perc(0.0005, decay, curve: -6),
        doneAction: 2);
    var click = SinOsc.ar(1800) * EnvGen.ar(Env.perc(0, 0.004));
    OffsetOut.ar(out, ((body + (click * 0.15)) * amp) ! 2);
}));

write.(SynthDef(\mph_snare, { |out = 0, amp = 1, gate = 1, seed = 1, rid = 0, decay = 0.18|
    var noise, tone;
    RandID.ir(rid);
    RandSeed.ir(1, seed);
    noise = BPF.ar(WhiteNoise.ar, 2500, 0.8) * EnvGen.ar(Env.perc(0.001, decay, curve: -5),
        doneAction: 2);
    tone = SinOsc.ar(EnvGen.ar(Env([240, 180], [0.03]))) * EnvGen.ar(Env.perc(0.001, 0.08));
    OffsetOut.ar(out, ((noise * 1.4) + (tone * 0.5) * amp) ! 2);
}));

write.(SynthDef(\mph_clap, { |out = 0, amp = 1, gate = 1, seed = 1, rid = 0, decay = 0.2|
    var bursts, sig;
    RandID.ir(rid);
    RandSeed.ir(1, seed);
    bursts = EnvGen.ar(Env([0, 1, 0.3, 1, 0.3, 1, 0], [0, 0.01, 0, 0.01, 0, decay],
        [0, -4, 0, -4, 0, -5]), doneAction: 2);
    sig = BPF.ar(WhiteNoise.ar, 1200, 0.6) * bursts * 2;
    OffsetOut.ar(out, (sig * amp) ! 2);
}));

write.(SynthDef(\mph_hat, { |out = 0, amp = 1, gate = 1, seed = 1, rid = 0, decay = 0.06|
    var sig, env, choke;
    RandID.ir(rid);
    RandSeed.ir(1, seed);
    env = EnvGen.ar(Env.perc(0.0005, decay, curve: -6), doneAction: 2);
    choke = EnvGen.ar(Env([1, 1, 0], [0, 0.005], releaseNode: 1), gate, doneAction: 2);
    sig = HPF.ar(WhiteNoise.ar, 7000) * env * choke * 0.8;
    OffsetOut.ar(out, (sig * amp) ! 2);
}));

write.(SynthDef(\mph_rim, { |out = 0, amp = 1, gate = 1, seed = 1, rid = 0, decay = 0.03|
    var sig = (SinOsc.ar(1700) + SinOsc.ar(480, 0, 0.5))
        * EnvGen.ar(Env.perc(0.0002, decay, curve: -6), doneAction: 2);
    OffsetOut.ar(out, (sig * 0.6 * amp) ! 2);
}));

write.(SynthDef(\mph_tom, { |out = 0, amp = 1, gate = 1, seed = 1, rid = 0, decay = 0.3|
    var pitch = EnvGen.ar(Env([210, 125], [0.12], \exp));
    var sig = SinOsc.ar(pitch) * EnvGen.ar(Env.perc(0.001, decay, curve: -5), doneAction: 2);
    OffsetOut.ar(out, (sig * 0.8 * amp) ! 2);
}));

write.(SynthDef(\mph_clave, { |out = 0, amp = 1, gate = 1, seed = 1, rid = 0, decay = 0.05|
    var sig = SinOsc.ar(2500) * EnvGen.ar(Env.perc(0.0002, decay, curve: -6),
        doneAction: 2);
    OffsetOut.ar(out, (sig * 0.6 * amp) ! 2);
}));

write.(SynthDef(\mph_conga, { |out = 0, amp = 1, gate = 1, seed = 1, rid = 0, decay = 0.22|
    var pitch = EnvGen.ar(Env([380, 320], [0.04], \exp));
    var sig = SinOsc.ar(pitch) * EnvGen.ar(Env.perc(0.001, decay, curve: -5), doneAction: 2);
    OffsetOut.ar(out, (sig * 0.7 * amp) ! 2);
}));

write.(SynthDef(\mph_shaker, { |out = 0, amp = 1, gate = 1, seed = 1, rid = 0, decay = 0.07|
    var sig;
    RandID.ir(rid);
    RandSeed.ir(1, seed);
    sig = HPF.ar(WhiteNoise.ar, 5000) * EnvGen.ar(Env.perc(0.006, decay, curve: -4),
        doneAction: 2);
    OffsetOut.ar(out, (sig * 0.5 * amp) ! 2);
}));

"mph: synthdefs written to %".format(dir).postln;
0.exit;
)
