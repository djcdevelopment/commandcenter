"""hearth.media — the constrained media-render lane (BF6 highlights, dual Arc Pro B70).

`lanes` establishes *physical* identity for the two B70s and binds it to the
indices ffmpeg actually accepts. Everything else in this package depends on that
binding being right, because Windows hands out a different adapter order to
every enumeration API that asks (see the module docstring).

The split mirrors hearth.health: pure, IO-free parsing and arithmetic so the
rules are unit-testable without hardware, with a thin IO shell that shells out
to vulkaninfo / ffmpeg / the performance counters.
"""
