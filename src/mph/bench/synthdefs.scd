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

// 303-style bass: a band-limited saw through a resonant ladder low-pass and
// soft saturation. The saw is a sum of its harmonics up to 20 kHz (fading out
// over the last 1 kHz, and never past 0.45 of the sample rate), so it has no
// aliasing of its own; SuperCollider's Saw.ar leaks about -50 dB of
// inharmonic energy (REQ-AUD-04). freq and acc are per note; a slide is an
// /n_set of freq on the held note, so nothing retriggers and Lag makes the
// glide. The controls (cutoff, resonance, envmod, decay, accent) are what the
// keyboard plays.
~acidHarmonics = 256;  // C2's harmonics reach 16.7 kHz
write.(SynthDef(\mph_acid, { |out = 0, amp = 1, gate = 1, freq = 65.41, acc = 0,
    cutoff = 420, resonance = 0.6, envmod = 0.6, decay = 0.3, accent = 0.6, glide = 0.06|
    var accented = Lag.kr(acc, 0.005) * accent;
    var pitch = Lag.kr(freq, glide);
    var fenv = EnvGen.ar(Env.perc(0.003, decay * (1 - (accent * acc * 0.5)), curve: -4));
    var wow = EnvGen.ar(Env.perc(0.02, 0.25, curve: -3)) * accented;
    var cf = (cutoff * (1 + (envmod * 7 * fenv) + (wow * 6))).clip(30, 18000);
    var top = min(20000, SampleRate.ir * 0.45);
    var saw = Mix.fill(~acidHarmonics, { |i|
        var f = pitch * (i + 1);
        SinOsc.ar(f, 0, ((top - f) / 1000).clip(0, 1) / (i + 1))
    }) * (2 / pi);
    var filtered = MoogFF.ar(saw, cf, resonance.linlin(0, 1, 0, 3.95));
    var level = EnvGen.ar(Env.asr(0.002, 1, 0.008), gate, doneAction: 2)
        * (0.6 + (accented * 0.6)) * amp;
    OffsetOut.ar(out, ((filtered * 1.5).tanh * level * 0.7) ! 2);
}));

// Pad (BM-SC-01): a chord of band-limited saws (root, third and fifth, each
// as two oscillators detuned apart) through a gentle low-pass. The saws are
// sums of harmonics, like the acid's, so there's no aliasing (REQ-AUD-04).
// chord 0 plays the root alone, and detune 0 one oscillator per note: the
// aliasing probe uses both, so the energy it measures is aliasing, not chord.
~padHarmonics = 40;
write.(SynthDef(\mph_pad, { |out = 0, amp = 1, gate = 1, freq = 130.81, acc = 0,
    cutoff = 1800, resonance = 0.2, third = 3, fifth = 7, chord = 1, detune = 8,
    attack = 0.06, release = 0.06|
    var top = min(20000, SampleRate.ir * 0.45);
    var saw = { |f|
        Mix.fill(~padHarmonics, { |i|
            var h = f * (i + 1);
            SinOsc.ar(h, 0, ((top - h) / 1000).clip(0, 1) / (i + 1))
        }) * (2 / pi)
    };
    var pair = { |f| (saw.(f * (detune / 100).midiratio.sqrt)
        + saw.(f / (detune / 100).midiratio.sqrt)) * 0.5 };
    var notes = pair.(freq) + ((pair.(freq * third.midiratio)
        + pair.(freq * fifth.midiratio)) * chord);
    var filtered = MoogFF.ar(notes / (1 + (2 * chord)), cutoff.clip(30, 18000),
        resonance.linlin(0, 1, 0, 3.5));
    var level = EnvGen.ar(Env.asr(attack, 1, release, \sin), gate, doneAction: 2) * amp;
    OffsetOut.ar(out, (filtered * level * 0.8) ! 2);
}));

// Sidechain (REQ-BEN-12). Each trigger hit starts an mph_duck at its own
// sample: it writes how much level to take away (0 to depth) to a private
// bus, rising over the attack and falling back along a half cosine over the
// release. An mph_ducker per target runs after the voices and scales that
// target's two channels by 1 minus the bus, never past the depth, so
// overlapping dips add up but stop at the full depth.
write.(SynthDef(\mph_duck, { |bus = 512, depth = 0.5, attack = 0.002, release = 0.2|
    var env = EnvGen.ar(Env([0, depth, 0], [attack, release], [\lin, \sin]), doneAction: 2);
    OffsetOut.ar(bus, env);
}));

write.(SynthDef(\mph_ducker, { |out = 0, bus = 512, depth = 0.5|
    var gain = 1 - In.ar(bus, 1).clip(0, depth);
    ReplaceOut.ar(out, In.ar(out, 2) * gain);
}));

"mph: synthdefs written to %".format(dir).postln;
0.exit;
)
