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

// West African drums (the North star benchmarks), until one-shots arrive.
// Bell: an iron bell struck with a stick, like an agogo or gankogui. A few
// inharmonic partials, the higher ones dying first, and a short click.
write.(SynthDef(\mph_bell, { |out = 0, amp = 1, gate = 1, seed = 1, rid = 0, freq = 820,
    decay = 0.25|
    var ratios = [1, 2.37, 3.94, 5.6];
    var levels = [1, 0.45, 0.25, 0.12];
    var rings = [1, 0.55, 0.35, 0.2];
    var body = Mix.fill(4, { |i|
        SinOsc.ar(freq * ratios[i]) * levels[i]
            * EnvGen.ar(Env.perc(0.0005, decay * rings[i], curve: -6),
                doneAction: if(i == 0) { 2 } { 0 })
    });
    var click;
    RandID.ir(rid);
    RandSeed.ir(1, seed);
    click = BPF.ar(WhiteNoise.ar, 3200, 0.6) * EnvGen.ar(Env.perc(0.0002, 0.006));
    OffsetOut.ar(out, (((body * 0.35) + (click * 0.4)) * amp) ! 2);
}));

// Djembe: one goblet drum with three strokes, set by stroke: 0 bass (the
// low centre of the skin), 1 tone (open, near the rim), 2 slap (bright and
// short, with a crack of noise). Tone and slap ring at the membrane's modes.
write.(SynthDef(\mph_djembe, { |out = 0, amp = 1, gate = 1, seed = 1, rid = 0, stroke = 1|
    var freq = Select.kr(stroke, [70, 330, 370]);
    var ring = Select.kr(stroke, [0.3, 0.18, 0.08]);
    var bright = Select.kr(stroke, [0.05, 0.4, 1.0]);
    var crack = Select.kr(stroke, [0, 0.15, 1.0]);
    var modes = [1, 1.594, 2.136, 2.296, 2.653];
    var levels = [1, 0.6, 0.45, 0.35, 0.25];
    var drop = EnvGen.ar(Env([1.4, 1], [0.03], \exp));  // the skin's pitch settles
    var body = Mix.fill(5, { |i|
        SinOsc.ar(freq * modes[i] * drop) * levels[i] * (if(i == 0) { 1 } { bright })
            * EnvGen.ar(Env.perc(0.001, ring / (1 + i), curve: -5))
    });
    var length = EnvGen.ar(Env([1, 1], [ring + 0.02]), doneAction: 2);
    var noise;
    RandID.ir(rid);
    RandSeed.ir(1, seed);
    noise = BPF.ar(WhiteNoise.ar, 2800, 0.7) * EnvGen.ar(Env.perc(0.0005, 0.03)) * crack;
    OffsetOut.ar(out, (((body * 0.5) + (noise * 0.6)) * length * amp) ! 2);
}));

// Dunun: a big two-headed bass drum played with a stick, the low line of a
// Mande ensemble. A low skin whose pitch falls as it rings, and the stick.
write.(SynthDef(\mph_dunun, { |out = 0, amp = 1, gate = 1, seed = 1, rid = 0, decay = 0.4|
    var pitch = EnvGen.ar(Env([150, 95, 88], [0.03, 0.2], \exp));
    var body = SinOsc.ar(pitch) * EnvGen.ar(Env.perc(0.001, decay, curve: -4), doneAction: 2);
    var skin = SinOsc.ar(pitch * 2.3) * EnvGen.ar(Env.perc(0.0005, 0.05)) * 0.3;
    var stick;
    RandID.ir(rid);
    RandSeed.ir(1, seed);
    stick = BPF.ar(WhiteNoise.ar, 1800, 0.6) * EnvGen.ar(Env.perc(0.0003, 0.015)) * 0.5;
    OffsetOut.ar(out, ((body + skin + stick) * 0.8 * amp) ! 2);
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

// Harmonic series for the new synths (REQ-AUD-04): sines at whole multiples
// of the pitch, each faded out over the last 1 kHz below 20 kHz (or 0.45 of
// the sample rate), so nothing aliases. A saw is every harmonic at 1/n, a
// square the odd ones at 1/n, a triangle the odd ones at 1/n squared with
// alternating signs.
~top = { min(20000, SampleRate.ir * 0.45) };
~partial = { |f, n, level, top| SinOsc.ar(f * n, 0, ((top - (f * n)) / 1000).clip(0, 1) * level) };
~saw = { |f, count, top| Mix.fill(count, { |i| ~partial.(f, i + 1, 1 / (i + 1), top) }) * (2 / pi) };
~square = { |f, count, top|
    Mix.fill(count, { |k| ~partial.(f, (2 * k) + 1, 1 / ((2 * k) + 1), top) }) * (4 / pi)
};
~triangle = { |f, count, top|
    Mix.fill(count, { |k|
        var n = (2 * k) + 1;
        ~partial.(f, n, ((-1) ** k) / (n * n), top)
    }) * (8 / (pi * pi))
};

// Bass (Love Train remix 1): a sine at the note's pitch under a band-limited
// saw through a resonant ladder low-pass with a short envelope, for a rolling
// Detroit line. No saturation, so nothing folds back at 48 kHz. An accent is
// louder and opens the filter further; a slide is an /n_set of freq.
write.(SynthDef(\mph_bass, { |out = 0, amp = 1, gate = 1, freq = 65.41, acc = 0,
    cutoff = 520, resonance = 0.25, envmod = 0.6, decay = 0.16, sub = 0.8, accent = 0.5,
    glide = 0.05|
    var top = ~top.value;
    var accented = Lag.kr(acc, 0.005) * accent;
    var pitch = Lag.kr(freq, glide);
    var saw = ~saw.(pitch, 96, top);
    var fenv = EnvGen.ar(Env.perc(0.002, decay * (1 + accented), curve: -4));
    var cf = (cutoff * (1 + (envmod * 6 * fenv) + (accented * 3 * fenv))).clip(30, 18000);
    var body = MoogFF.ar(saw, cf, resonance.linlin(0, 1, 0, 3.6));
    var low = SinOsc.ar(pitch) * sub;
    var level = EnvGen.ar(Env.adsr(0.002, 0.25, 0.8, 0.02), gate, doneAction: 2)
        * (0.7 + (accented * 0.6)) * amp;
    OffsetOut.ar(out, (((body * 1.2) + low) * level * 0.5) ! 2);
}));

// Organ bass (BM-BASS-02, the Korg M1's "Organ 2"): a triangle-saw blend at
// the note, a square three octaves up and a triangle a fifth above, with a
// percussive third harmonic at the attack and a fast filter envelope. The
// chorus (two slowly moving delays, one per side) takes only what is above
// 150 Hz, so the low end stays mono. fifth 0 and chorus 0 leave only the
// note's own harmonics, for the aliasing probe.
write.(SynthDef(\mph_organ, { |out = 0, amp = 1, gate = 1, freq = 65.41, acc = 0,
    cutoff = 1400, resonance = 0.15, envmod = 2.5, decay = 0.12, chorus = 0.3, accent = 0.4,
    glide = 0, fifth = 0.3|
    var top = ~top.value;
    var accented = Lag.kr(acc, 0.005) * accent;
    var pitch = Lag.kr(freq, glide);
    var blend = (~triangle.(pitch, 16, top) * 0.6) + (~saw.(pitch, 48, top) * 0.4);
    var upper = ~square.(pitch * 8, 12, top) * 0.22;
    var above = ~triangle.(pitch * 1.5, 8, top) * fifth;
    var click = SinOsc.ar(pitch * 3) * EnvGen.ar(Env.perc(0.001, 0.08, curve: -6)) * 0.3;
    var fenv = EnvGen.ar(Env.perc(0.002, decay * (1 + accented), curve: -5));
    var cf = (cutoff * (1 + (envmod * fenv) + (accented * 2 * fenv))).clip(30, 18000);
    var sig = MoogFF.ar(blend + upper + above + click, cf, resonance.linlin(0, 1, 0, 3.2));
    var low = LPF.ar(LPF.ar(sig, 150), 150);
    var high = sig - low;
    var sides = [0.0071, 0.0083].collect { |base, i|
        DelayC.ar(high, 0.02, base + SinOsc.ar([0.61, 0.83][i], i * 1.57, 0.0015))
    };
    var level = EnvGen.ar(Env.adsr(0.003, 0.2, 0.85, 0.03), gate, doneAction: 2)
        * (0.75 + (accented * 0.5)) * amp;
    var wide = sides.collect { |side| low + (high * (1 - chorus)) + (side * chorus) };
    OffsetOut.ar(out, wide * level * 0.5);
}));

// Chord stab (Detroit chord memory, BM-KEYS-01): two saws detuned apart, one
// per side, and a square, through ladder low-passes with a fast envelope. A
// chord is one synth per note (REQ-BEN-18). detune 0 makes both sides the
// same, so the aliasing probe hears one pitch.
write.(SynthDef(\mph_stab, { |out = 0, amp = 1, gate = 1, freq = 261.63, acc = 0,
    cutoff = 900, resonance = 0.3, envmod = 4, decay = 0.22, detune = 12, square = 0.35,
    accent = 0.5, release = 0.12, width = 0.6|
    var top = ~top.value;
    var accented = Lag.kr(acc, 0.005) * accent;
    var ratio = (detune / 100).midiratio.sqrt;
    var hollow = ~square.(freq, 20, top) * square;
    var sides = [freq * ratio, freq / ratio].collect { |f| ~saw.(f, 40, top) + hollow };
    var fenv = EnvGen.ar(Env.perc(0.001, decay * (1 + (accented * 0.5)), curve: -5));
    var cf = (cutoff * (1 + (envmod * fenv) + (accented * 3 * fenv))).clip(30, 18000);
    var res = resonance.linlin(0, 1, 0, 3.5);
    var filtered = sides.collect { |side| MoogFF.ar(side, cf, res) };
    var mid = (filtered[0] + filtered[1]) * 0.5;
    var side = (filtered[0] - filtered[1]) * 0.5 * width;
    var level = EnvGen.ar(Env.adsr(0.002, decay * 1.5, 0.35, release), gate, doneAction: 2)
        * (0.6 + (accented * 0.5)) * amp;
    OffsetOut.ar(out, [mid + side, mid - side] * level * 0.5);
}));

// Strings: three band-limited saws per note, detuned and spread across the
// sides, with a slow attack, a vibrato that fades in after the attack, a
// gentle low-pass and a long release. detune 0 and vibrato 0 give one steady
// pitch, for the aliasing probe.
write.(SynthDef(\mph_strings, { |out = 0, amp = 1, gate = 1, freq = 261.63, acc = 0,
    cutoff = 5000, resonance = 0.1, attack = 0.45, release = 1.1, detune = 14,
    vibrato = 0.12, speed = 5.2, width = 0.8|
    var top = ~top.value;
    var depth = EnvGen.kr(Env([0, 0, 1], [0.35, 0.6])) * vibrato;
    var pitch = freq * SinOsc.kr(speed, 0, depth).midiratio;
    var ratio = (detune / 100).midiratio;
    var centre = ~saw.(pitch, 24, top) * 0.7;
    var res = resonance.linlin(0, 1, 0, 3);
    var sides = [pitch / ratio, pitch * ratio].collect { |f|
        MoogFF.ar(~saw.(f, 24, top) + centre, cutoff.clip(30, 18000), res)
    };
    var mid = (sides[0] + sides[1]) * 0.5;
    var side = (sides[0] - sides[1]) * 0.5 * width;
    var level = EnvGen.ar(Env.asr(attack, 1, release, \sin), gate, doneAction: 2) * amp;
    OffsetOut.ar(out, [mid + side, mid - side] * level * 0.35);
}));

// The mix (spec/components/mix.md). In a mixed render each voice writes to a
// private bus pair; mph_tap copies one pair to output channels (a stem, or a
// sidechain target before its dip).
write.(SynthDef(\mph_tap, { |in = 600, out = 0|
    Out.ar(out, In.ar(in, 2));
}));

// A channel strip (REQ-MIX-01): 24 dB/octave high- and low-pass filters, each
// switched in only when used (hp, lp), a balance, a level in dB, and up to four
// sends, taken after the level. At -90 dB or lower a level is silence. Every
// control is smoothed over 5 ms, so jumps in automation don't click.
write.(SynthDef(\mph_strip, { |in = 600, out = 0, hp = 0, highpass = 20, lp = 0,
    lowpass = 20000, resonance = 0, level = 0, pan = 0,
    bus1 = 1020, send1 = -90, bus2 = 1020, send2 = -90, bus3 = 1020, send3 = -90,
    bus4 = 1020, send4 = -90|
    var dry = In.ar(in, 2);
    var ceiling = SampleRate.ir * 0.45;
    var hpf = Lag.kr(highpass, 0.005).clip(10, ceiling);
    var lpf = Lag.kr(lowpass, 0.005).clip(10, ceiling);
    var rq = (1 - (resonance * 0.85)) * 2.sqrt;
    var gain = { |db| Lag.ar(K2A.ar(Select.kr(db > -90, [0, db.dbamp])), 0.005) };
    var low = BHiPass4.ar(dry, hpf, 2.sqrt);
    var sig = 2.collect { |c| Select.ar(hp, [dry[c], low[c]]) };
    var high = BLowPass4.ar(sig, lpf, rq);
    sig = 2.collect { |c| Select.ar(lp, [sig[c], high[c]]) };
    sig = sig * [(1 - pan).clip(0, 1), (1 + pan).clip(0, 1)] * gain.(level);
    Out.ar(out, sig);
    [[bus1, send1], [bus2, send2], [bus3, send3], [bus4, send4]].do { |pair|
        Out.ar(pair[0], sig * gain.(pair[1]))
    };
}));

// Automation (REQ-MIX-03): a straight line on a control bus, in dB (mph_line)
// or evenly in octaves (mph_xline). The bus keeps its last value after.
write.(SynthDef(\mph_line, { |bus = 0, from = 0, to = 0, dur = 1|
    ReplaceOut.kr(bus, Line.kr(from, to, dur, doneAction: 2));
}));

write.(SynthDef(\mph_xline, { |bus = 0, from = 100, to = 1000, dur = 1|
    ReplaceOut.kr(bus, XLine.kr(from, to, dur, doneAction: 2));
}));

// Send effects (REQ-MIX-02). The delay: an echo `time` after the input, then
// one `time` after each echo, each through a low-pass and an 80 Hz high-pass
// once more and `feedback` times as loud as the one before. LocalIn hears the
// loop one block late, so the loop's own delay line is one block shorter.
// With pingpong the first echo is the input in mono on the left, and each
// echo swaps sides.
write.(SynthDef(\mph_delay, { |in = 800, out = 802, time = 0.375, feedback = 0.4,
    lowpass = 3000, pingpong = 0|
    var input = In.ar(in, 2);
    var mono = (input[0] + input[1]) * 0.5;
    var first = [Select.ar(pingpong, [input[0], mono]), Select.ar(pingpong, [input[1], DC.ar(0)])];
    var back = LocalIn.ar(2);
    var delayed = DelayC.ar(first, 4, time) + DelayC.ar(back, 4, (time - ControlDur.ir).max(0));
    var echoes = HPF.ar(LPF.ar(delayed, lowpass.clip(20, SampleRate.ir * 0.45)), 80);
    var looped = echoes * feedback;
    LocalOut.ar([Select.ar(pingpong, [looped[0], looped[1]]),
        Select.ar(pingpong, [looped[1], looped[0]])]);
    Out.ar(out, echoes);
}));

// The hall: a predelay, four allpasses per side to diffuse the input, then a
// feedback delay network of eight lines (1031 to 3089 samples at 48 kHz)
// mixed by an orthogonal 8 x 8 Hadamard matrix. Each line's gain gives the
// decay time to -60 dB, a low-pass in each line makes highs die faster
// (damping), and each line's length drifts by up to 0.35 ms on its own slow
// sine, so the tail doesn't ring. Nothing is random (REQ-AUD-05).
write.(SynthDef(\mph_reverb, { |in = 800, out = 802, decay = 3, damping = 6000,
    predelay = 0.02|
    var input = DelayC.ar(In.ar(in, 2), 0.5, predelay);
    var lengths = [1031, 1327, 1523, 1801, 2063, 2399, 2731, 3089] / 48000;
    var diffuse = { |sig, stretch|
        [0.0047, 0.0036, 0.0127, 0.0093].inject(sig, { |x, t|
            AllpassC.ar(x, 0.05, t * stretch, t * stretch * 13.52)
        })
    };
    var sides = [diffuse.(input[0], 1), diffuse.(input[1], 1.07)];
    var hadamard = { |x|
        var y = x.copy;
        [1, 2, 4].do { |h|
            var z = y.copy;
            (0, (2 * h) .. 7).do { |i|
                h.do { |j|
                    z[i + j] = y[i + j] + y[i + j + h];
                    z[i + j + h] = y[i + j] - y[i + j + h];
                };
            };
            y = z;
        };
        y * 8.sqrt.reciprocal
    };
    var mixed = hadamard.(LocalIn.ar(8));
    var signs = [1, 1, -1, -1, 1, 1, -1, -1];
    var lines = 8.collect { |i|
        var t = lengths[i];
        var drift = SinOsc.ar(0.07 + (i * 0.031), i * 0.785, 0.00035);
        var line = DelayC.ar(mixed[i] + (sides[i % 2] * signs[i]), 0.1,
            t - ControlDur.ir + drift);
        LPF.ar(line, damping.clip(200, SampleRate.ir * 0.45)) * (10 ** (-3 * t / decay))
    };
    LocalOut.ar(lines);
    Out.ar(out, [
        (lines[0] - lines[2] + lines[4] - lines[6]) * 0.3,
        (lines[1] - lines[3] + lines[5] - lines[7]) * 0.3
    ]);
}));

// Sidechain (REQ-BEN-12). Each trigger hit starts an mph_duck at its own
// sample: it writes how much level to take away (0 to depth) to a private
// bus, rising over the attack, staying at the depth for the hold, and
// falling back along a half cosine over the release. An mph_ducker per
// target runs after the voices and scales that target's two channels by
// 1 minus the bus, never past the depth, so overlapping dips add up but
// stop at the full depth.
write.(SynthDef(\mph_duck, { |bus = 512, depth = 0.5, attack = 0.002, hold = 0, release = 0.2|
    var env = EnvGen.ar(
        Env([0, depth, depth, 0], [attack, hold, release], [\lin, \lin, \sin]), doneAction: 2);
    OffsetOut.ar(bus, env);
}));

write.(SynthDef(\mph_ducker, { |out = 0, bus = 512, depth = 0.5|
    var gain = 1 - In.ar(bus, 1).clip(0, depth);
    ReplaceOut.ar(out, In.ar(out, 2) * gain);
}));

"mph: synthdefs written to %".format(dir).postln;
0.exit;
)
