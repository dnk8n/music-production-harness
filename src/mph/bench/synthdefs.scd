// SynthDefs for benchmark renders (spec/components/benchmarks.md, ADR-0002).
// Run as: sclang synthdefs.scd <output folder>
// mph compiles this once per change and caches the .scsyndef files in data_dir.
//
// Output goes through OffsetOut so hits land on their exact sample, not the
// start of a 64-sample block.
// Every voice: out (first of two channels), amp, gate (0 chokes it), seed.
// Noise is seeded per hit: each synth takes its own random generator (rid: a
// drum hit one of scsynth's first 64, a note one of its own above them) and
// seeds it, so every hit of a voice sounds the same and renders are
// bit-identical (REQ-AUD-05). Sharing a generator would make the noise depend
// on which other synths happen to be playing.
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

// A 909-style kit (the free version of Love Train remix 1, issue #68). Kick: a
// sine whose pitch drops from five times its tune within 60 ms, driven a little
// into tanh for punch, with a click of band-passed noise. Its body is down
// 18 dB 150 ms after it hits, so a duck is heard after it. Its level ends at 0,
// so it stops without a click.
write.(SynthDef(\mph_kick909, { |out = 0, amp = 1, gate = 1, seed = 1, rid = 0,
    tune = 50, decay = 0.45, punch = 1.8, click = 0.35|
    var pitch = EnvGen.ar(Env([tune * 5, tune * 1.7, tune], [0.0035, 0.055], \exp));
    var level = EnvGen.ar(Env([0, 1, 1, 0.001, 0], [0.0004, 0.012, decay, 0.004],
        [0, 0, \exp, 0]), doneAction: 2);
    var body = (SinOsc.ar(pitch) * punch).tanh;
    var noise;
    RandID.ir(rid);
    RandSeed.ir(1, seed);
    noise = LPF.ar(HPF.ar(WhiteNoise.ar, 1500), 9000) * EnvGen.ar(Env.perc(0.0002, 0.006,
        curve: -4));
    OffsetOut.ar(out, (((body * level) + (noise * click)) * amp * 0.8) ! 2);
}));

// 909-style clap: band-passed noise struck four times, about 9 ms apart, then
// a short tail.
write.(SynthDef(\mph_clap909, { |out = 0, amp = 1, gate = 1, seed = 1, rid = 0,
    decay = 0.17, tone = 1150|
    var noise, env, sig;
    RandID.ir(rid);
    RandSeed.ir(1, seed);
    noise = WhiteNoise.ar;
    env = EnvGen.ar(Env(
        [0, 1, 0.12, 1, 0.12, 1, 0.12, 1, 0],
        [0.0003, 0.0087, 0.0003, 0.0087, 0.0003, 0.0087, 0.0003, decay],
        [0, -4, 0, -4, 0, -4, 0, -4]), doneAction: 2);
    sig = (BPF.ar(noise, tone, 0.6) * 2) + (BPF.ar(noise, tone * 2.2, 0.9) * 0.6);
    OffsetOut.ar(out, (sig * env * amp * 0.6) ! 2);
}));

// 909-style hat: mostly hiss, with some metal from six band-limited squares
// (pulses at the 808's ratios), high-passed. It hisses rather than rings
// (spectral flatness about 0.37 above 2 kHz, REQ-BEN-05). gate 0 chokes it.
write.(SynthDef(\mph_hat909, { |out = 0, amp = 1, gate = 1, seed = 1, rid = 0,
    decay = 0.05, metal = 0.42, tone = 9000|
    var freqs = [205.3, 304.4, 369.6, 522.7, 540.0, 800.0] * 1.43;
    var metallic = Mix(Pulse.ar(freqs, 0.5)) / 6;
    var noise, src, sig, env, choke;
    RandID.ir(rid);
    RandSeed.ir(1, seed);
    noise = WhiteNoise.ar;
    src = (noise * (1 - metal)) + (metallic * metal * 1.6);
    sig = HPF.ar(BPF.ar(src, tone, 1.4) + (HPF.ar(src, 7000) * 0.5), 6000);
    env = EnvGen.ar(Env.perc(0.0005, decay, curve: -6), doneAction: 2);
    choke = EnvGen.ar(Env([1, 1, 0], [0, 0.005], releaseNode: 1), gate, doneAction: 2);
    OffsetOut.ar(out, (sig * env * choke * amp * 0.9) ! 2);
}));

// 909-style ride: a wash of noise and metal, and a faint bell ping on top.
write.(SynthDef(\mph_ride909, { |out = 0, amp = 1, gate = 1, seed = 1, rid = 0,
    decay = 1.3, ping = 0.35, metal = 0.6|
    var freqs = [205.3, 304.4, 369.6, 522.7, 540.0, 800.0] * 2.17;
    var metallic = Mix(Pulse.ar(freqs, 0.5)) / 6;
    var noise, wash, bell, env, choke;
    RandID.ir(rid);
    RandSeed.ir(1, seed);
    noise = WhiteNoise.ar;
    wash = BPF.ar((noise * (1 - metal)) + (metallic * metal * 1.6), 6800, 1.2)
        + (HPF.ar(noise, 9000) * 0.25);
    bell = Klank.ar(`[[3150, 4720, 5340, 6950, 8260], [1, 0.7, 0.6, 0.4, 0.25],
        [0.8, 0.6, 0.55, 0.4, 0.3]], Impulse.ar(0) * 0.02);
    env = EnvGen.ar(Env.perc(0.0008, decay, curve: -5), doneAction: 2);
    choke = EnvGen.ar(Env([1, 1, 0], [0, 0.01], releaseNode: 1), gate, doneAction: 2);
    OffsetOut.ar(out, ((HPF.ar(wash, 2500) + (bell * ping)) * env * choke * amp * 0.8) ! 2);
}));

// 909-style rim shot: resonators near 480 Hz and 1.45 kHz, struck once.
write.(SynthDef(\mph_rim909, { |out = 0, amp = 1, gate = 1, seed = 1, rid = 0, decay = 0.045|
    var exc, sig;
    RandID.ir(rid);
    RandSeed.ir(1, seed);
    exc = (Impulse.ar(0) * 0.3) + (HPF.ar(WhiteNoise.ar, 2000) * EnvGen.ar(Env.perc(0, 0.0015))
        * 0.08);
    sig = Klank.ar(`[[480, 1450, 2960], [0.45, 1, 0.25], [decay, decay * 0.75, decay * 0.4]], exc);
    sig = (sig * 3).tanh * EnvGen.ar(Env([1, 1, 0], [decay * 2, 0.005]), doneAction: 2);
    OffsetOut.ar(out, (sig * amp * 0.6) ! 2);
}));

// A garage shaker: beads (seeded dust) that swell and thin out, through a
// high resonance.
write.(SynthDef(\mph_beads, { |out = 0, amp = 1, gate = 1, seed = 1, rid = 0,
    decay = 0.09, tone = 6500, attack = 0.012|
    var energy, beads, sig;
    RandID.ir(rid);
    RandSeed.ir(1, seed);
    energy = EnvGen.ar(Env([0, 1, 0], [attack, decay], [2, -4]), doneAction: 2);
    beads = Dust2.ar(9000) * energy;
    sig = BPF.ar(beads, tone, 0.6) + (HPF.ar(beads, 9000) * 0.3)
        + (BPF.ar(WhiteNoise.ar, tone, 0.8) * energy * 0.25);
    OffsetOut.ar(out, (sig * amp) ! 2);
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

// Singing voices (the free version of Love Train remix 1, issue #68). A sung
// note gives its syllable as two numbers (REQ-BEN-19): vowel (0 to 4 are a e i
// o u, 5 a hum) and onset, its consonant (0 is none, then h y w l m n d b). A
// voice is a buzz (Blip, a band-limited pulse train) and breath through five
// formant filters, a tenor's (the Csound manual's table: centre Hz, level dB,
// bandwidth Hz). The hum is an m held through the nose.
~vowels = [
    [[650, 1080, 2650, 2900, 3250], [0, -6, -7, -8, -22], [80, 90, 120, 130, 140]],
    [[400, 1700, 2600, 3200, 3580], [0, -14, -12, -14, -20], [70, 80, 100, 120, 120]],
    [[290, 1870, 2800, 3250, 3540], [0, -15, -18, -20, -30], [40, 90, 100, 120, 120]],
    [[400, 800, 2600, 2800, 3000], [0, -10, -12, -12, -26], [40, 80, 100, 120, 120]],
    [[350, 600, 2700, 2900, 3300], [0, -20, -17, -14, -26], [40, 60, 100, 120, 120]],
    [[250, 1150, 2400, 3100, 3600], [0, -24, -30, -34, -40], [60, 120, 150, 150, 150]]
];
// Each consonant: where its first three formants start (nil: at the vowel's),
// how long they take to reach the vowel's, the level the vowel opens from, a
// noise (centre Hz, level, length s; centre 0 breathes through the vowel's own
// formants), how long the voicing waits, and how long a nasal murmur lasts.
~onsets = [
    [nil, 0.0, 1.0, [3000, 0, 0.001], 0.0, 0.0],                      // none
    [nil, 0.0, 1.0, [0, 0.22, 0.07], 0.055, 0.0],                     // h
    [[260, 2250, 3000], 0.09, 0.5, [3000, 0, 0.001], 0.0, 0.0],       // y
    [[300, 610, 2200], 0.09, 0.4, [3000, 0, 0.001], 0.0, 0.0],        // w
    [[360, 1050, 2800], 0.07, 0.6, [3000, 0, 0.001], 0.0, 0.0],       // l
    [[250, 1000, 2400], 0.05, 1.0, [3000, 0, 0.001], 0.0, 0.07],      // m
    [[250, 1700, 2600], 0.05, 1.0, [3000, 0, 0.001], 0.0, 0.06],      // n
    [[300, 1800, 2700], 0.045, 1.0, [3800, 0.5, 0.014], 0.022, 0.0],  // d
    [[300, 900, 2300], 0.045, 1.0, [1100, 0.3, 0.01], 0.024, 0.0]     // b
];

// A syllable's formants as they move from its consonant to its vowel.
~formants = { |vowel, onset|
    var target = 5.collect { |k| Select.kr(vowel, ~vowels.collect { |v| v[0][k] }) };
    var gains = 5.collect { |k| Select.kr(vowel, ~vowels.collect { |v| v[1][k] }) };
    var widths = 5.collect { |k| Select.kr(vowel, ~vowels.collect { |v| v[2][k] }) };
    var move = Select.kr(onset, ~onsets.collect { |o| o[1] });
    var delay = Select.kr(onset, ~onsets.collect { |o| o[4] + o[5] });
    var freqs = 5.collect { |k|
        var start = if(k < 3) {
            Select.kr(onset, ~onsets.collect { |o| if(o[0].isNil) { -1 } { o[0][k] } })
        } { nil };
        var from = if(start.isNil) { target[k] } { Select.kr(start < 0, [start, target[k]]) };
        EnvGen.kr(Env([from, from, target[k]], [delay, move.max(0.001)], [0, -3]))
    };
    [freqs, gains, widths]
};

// One voice: the buzz and breath through the formants, with the consonant at
// the start (a burst of noise, a murmur through the nose, or formants on the
// move), rolled off above 6.5 kHz. bright lifts the upper three formants (dB),
// and waver 0 holds the formants still. The buzz spreads its power over up to
// 200 harmonics and the formants pass only a few of them, so the voice is
// made up by 32 dB: a note then sounds about as loud as the other synths' do,
// well inside the gain that level matching gives (REQ-BEN-09).
~voice = { |pitch, vowel, onset, breath, bright, waver|
    var formants = ~formants.(vowel, onset);
    var top = ~top.value;
    var buzz = Blip.ar(pitch, (top / pitch).floor.min(200));
    var noise = 3.collect { |k| Select.kr(onset, ~onsets.collect { |o| o[3][k] }) };
    var wait = Select.kr(onset, ~onsets.collect { |o| o[4] });
    var murmur = Select.kr(onset, ~onsets.collect { |o| o[5] });
    var opens = Select.kr(onset, ~onsets.collect { |o| o[2] });
    var voicing = EnvGen.ar(Env([0, 0, 1], [wait, 0.012], [0, 2]));
    var opening = EnvGen.ar(Env([opens, opens, 1], [murmur, 0.06], [0, 2]));
    var aspirated = EnvGen.ar(Env([1, 1, 0], [noise[2], 0.03])) * (noise[0] <= 0) * noise[1];
    var source = (buzz * voicing)
        + (WhiteNoise.ar * (0.6 + (0.4 * LFPulse.ar(pitch, 0, 0.4))) * (breath + aspirated));
    var shaped = Mix.fill(5, { |k|
        var level = (formants[1][k] + if(k >= 2) { bright } { 0 }).dbamp;
        var centre = formants[0][k] * (1 + LFNoise1.kr(0.4 + (k * 0.13), 0.012 * waver));
        BPF.ar(source, centre.clip(80, 12000), (formants[2][k] / formants[0][k]).clip(0.005, 2))
            * level
    });
    var nasal = LPF.ar(LPF.ar(buzz * voicing, 380), 380) * 0.5;
    var nosed = EnvGen.ar(Env([1, 1, 0], [murmur, 0.03])) * (murmur > 0);
    var burst = BPF.ar(WhiteNoise.ar, noise[0].max(100), 0.5) * noise[1] * (noise[0] > 0)
        * EnvGen.ar(Env([0, 0, 1, 0], [wait - noise[2], 0.001, noise[2]].max(0.0005), [0, 0, -4]));
    ((BLowPass4.ar((shaped * (1 - nosed)) + (nasal * nosed), 6500, 1) * opening) + burst) * 40
};

// A soulful lead or a backing voice: the voice above at the note's pitch,
// scooping up into each note, with a vibrato that fades in, a little waver in
// pitch and level, and breath. An accent is louder and brighter. vibrato 0 and
// waver 0 hold the pitch still, and breath 0 leaves the buzz alone (the
// aliasing probe sets all three). Each part of a chord sings a little
// differently. A slide is an /n_set of freq, which glides.
write.(SynthDef(\mph_singer, { |out = 0, amp = 1, gate = 1, freq = 220, acc = 0, seed = 1,
    rid = 0, part = 0, vowel = 0, onset = 0, attack = 0.05, release = 0.18, vibrato = 0.35,
    rate = 5.4, vdelay = 0.28, scoop = 0.7, glide = 0.07, breath = 0.04, bright = 0,
    accent = 0.5, waver = 1|
    var accented, vib, drift, pitch, shimmer, sig, level;
    RandID.ir(rid);
    RandSeed.ir(1, seed + (part * 17));
    accented = acc * accent;
    vib = SinOsc.kr(rate * (1 + (part * 0.031)) + LFNoise1.kr(0.6, 0.25), part * 1.3)
        * EnvGen.kr(Env([0, 0, 1], [vdelay, 0.45], [0, 2])) * vibrato;
    drift = (LFNoise1.kr(8, 0.05) + LFNoise1.kr(1.7, 0.07)) * waver;
    pitch = Lag.kr(freq, glide)
        * (vib + drift - (EnvGen.kr(Env([1, 0], [0.09], -3)) * scoop)).midiratio;
    shimmer = 1 + LFNoise1.kr(6, 0.05 * waver);
    sig = ~voice.(pitch, vowel, onset, breath, bright + (accented * 5), waver);
    level = EnvGen.ar(Env.asr(attack, 1, release, [2, -3]), gate, doneAction: 2)
        * (0.75 + (accented * 0.8)) * shimmer * amp;
    OffsetOut.ar(out, (sig * level * 0.5) ! 2);
}));

// A vocoder lead (the robot voice): the voice above sings the syllable at the
// note's pitch, and 24 bands from 100 Hz to 8 kHz follow it, each opening the
// same band of a carrier: two band-limited saws detuned apart, one per side,
// and a square, at the note's pitch. The voice's breath comes through as hiss.
// The voice sings at the carrier's own pitch, so the bands' ripple lands on the
// carrier's harmonics. Each note scoops up into its pitch (scoop semitones
// below it), as a talk box player's does. An accent is louder, and brighter
// in the voice. detune 0, breath 0, hiss 0 and waver 0 leave one steady
// pitch, for the aliasing probe.
~bands = Array.geom(24, 100, (8000 / 100) ** (1 / 23));
write.(SynthDef(\mph_vocoder, { |out = 0, amp = 1, gate = 1, freq = 220, acc = 0, seed = 1,
    rid = 0, part = 0, vowel = 0, onset = 0, attack = 0.02, release = 0.15, glide = 0.04,
    scoop = 0.3, detune = 10, square = 0.4, hiss = 0.12, breath = 0.08, accent = 0.5,
    width = 0.7, waver = 1|
    var top = ~top.value;
    var accented = acc * accent;
    var pitch = Lag.kr(freq, glide) * (EnvGen.kr(Env([1, 0], [0.09], -3)) * scoop).neg.midiratio;
    var ratio = (detune / 100).midiratio.sqrt;
    var modulator, carriers, sides, sibilance, mid, side, level;
    RandID.ir(rid);
    RandSeed.ir(1, seed + (part * 17));
    modulator = ~voice.(pitch * (1 + LFNoise1.kr(3, 0.01 * waver)), vowel, onset, breath,
        2 + (accented * 6), waver);
    carriers = [pitch * ratio, pitch / ratio].collect { |f|
        (~saw.(f, 60, top) * 0.7) + (~square.(f, 30, top) * square)
    };
    sides = carriers.collect { |carrier|
        Mix.fill(~bands.size, { |b|
            var fc = ~bands[b];
            var follow = Amplitude.ar(BPF.ar(modulator, fc, 0.16), 0.003, 0.035);
            BPF.ar(BPF.ar(carrier, fc, 0.16), fc, 0.16) * follow * (fc / 400).sqrt.clip(0.7, 2.5)
        })
    };
    sibilance = HPF.ar(HPF.ar(modulator, 5500), 5500) * hiss;
    mid = ((sides[0] + sides[1]) * 0.5) + sibilance;
    side = (sides[0] - sides[1]) * 0.5 * width;
    level = EnvGen.ar(Env.asr(attack, 1, release, [2, -3]), gate, doneAction: 2)
        * (0.75 + (accented * 0.8)) * amp * 3;
    OffsetOut.ar(out, [mid + side, mid - side] * level);
}));

// A passing train horn (Love Train remix 1's signature sound, synthesised for
// its free version, issue #68), built from what a real air horn does. Each
// chime is two reeds (a band-limited saw and square) a few cents apart, one per
// side, through a bell: a high-pass under the note, two brassy resonances and a
// breath of air (air). A chord is one synth per chime, and each chime is tuned
// a few cents off the others (by part), as real chimes are, so the chord beats.
// A note settles in: it starts a quarter of a semitone flat with a rough
// growl an octave down, both gone in 0.3 s (growl). The train swells nearer
// (for swell seconds), passes (at pass seconds) and falls (bend semitones over
// bend_s seconds, a Doppler fall that moves the bell's resonances with the
// notes, as a real one does) as it fades away. Distance darkens it, and
// the ground's reflection arrives a few milliseconds late, more so as the
// train nears, so a comb sweeps through it as it passes (ground). An accent is
// louder and brighter. detune 0, air 0, growl 0 and ground 0 leave one steady
// reed, for the aliasing probe.
write.(SynthDef(\mph_horn, { |out = 0, amp = 1, gate = 1, freq = 164.81, acc = 0, seed = 1,
    rid = 0, part = 0, swell = 4.5, pass = 5.5, bend = -5, bend_s = 1.2, release = 0.6,
    detune = 7, brass = 1300, accent = 0.5, width = 0.5, air = 0.05, growl = 1, ground = 0.5|
    var top = ~top.value;
    var accented = acc * accent;
    var near = EnvGen.ar(Env([0.02, 1, 1, 0.2], [swell, (pass - swell).max(0.01), bend_s],
        [3, 0, -2]));
    var doppler = EnvGen.kr(Env([0, 0, bend], [pass, bend_s], [0, -2])).midiratio;
    var settle = EnvGen.kr(Env([1, 0], [0.3], [-3])) * growl;
    var cents = Select.kr(part.clip(0, 4), [0, 5, -4, 3, -6]) * (detune / 7);
    var ratio = (detune / 100).midiratio.sqrt;
    var pitch = freq * doppler * ((cents / 100) - (settle * 0.25)).midiratio;
    var bright = (near.linexp(0.02, 1, 500, 6000) * (1 + accented)).min(18000);
    var reeds, sig, mid, side, level;
    RandID.ir(rid);
    RandSeed.ir(1, seed + (part * 17));
    reeds = [ratio, ratio.reciprocal].collect { |r, i|
        var f = pitch * r * (1 + LFNoise1.kr([4, 3.3][i], 0.0003 * detune));
        var rough = 1 + (SinOsc.ar(f * 0.5) * settle * 0.5);
        ((~saw.(f, 50, top) * 0.55) + (~square.(f, 25, top) * 0.45)) * rough
    };
    sig = reeds.collect { |reed|
        var horn = brass * doppler;
        var bell = HPF.ar((BPF.ar(reed, horn, 0.6) * 3) + (BPF.ar(reed, horn * 2.3, 0.9) * 0.8)
            + (reed * 0.5), freq * doppler * 0.7);
        var heard = LPF.ar(bell + BPF.ar(WhiteNoise.ar(air), horn * 1.6, 1.2), bright);
        heard + (DelayC.ar(heard, 0.012, 0.0004 + (near.squared * 0.0065)) * ground * 0.7)
    };
    mid = (sig[0] + sig[1]) * 0.5;
    side = (sig[0] - sig[1]) * 0.5 * width;
    level = EnvGen.ar(Env.asr(0.08, 1, release, [2, -3]), gate, doneAction: 2) * near
        * (0.75 + (accented * 0.8)) * amp * 0.35;
    OffsetOut.ar(out, [mid + side, mid - side] * level);
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
