# Attribution and licensing

This widget is a port of [Fugleramme](https://github.com/arnegiacomo/fugleramme)
by Arne Giacomo Munthe-Kaas, made with his permission and named after it. It
draws the page with Fugleramme's own renderer, at release v0.29.0 (commit
`03046c764748a74bd6ffddf5ed526ceb0c199436`). Fugleramme itself is maintained
upstream; this widget is maintained separately.

## Code

MIT, see [`LICENSE`](LICENSE). `fugleramme/` is Fugleramme's code, copyright
Arne Giacomo Munthe-Kaas; each file there says what was changed for Tesserae.
`server.py`, `client.js`, `glue/` and `fugleramme/render/cmap.py` are the
widget's own.

## Bird plates

The plates are Fugleramme's `classic` style: hand-coloured 1800s plates by John
Gould, the von Wright brothers, J. J. Audubon, H. E. Dresser and many more, cut
out and edited for Fugleramme. **The style is offered under
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/)**, the most
restrictive of its sources. No plate is AI-generated, and nothing in this
widget generates one.

The plates are not in this repository or its release. The widget fetches each
one from Fugleramme's repository at the pinned commit the first time a page
draws it, and keeps it in its data folder. Every page carries a credit line
naming the artists of the plates on it and Fugleramme (it can be turned off in
the cell's options).

- [`assets/artwork/classic/ATTRIBUTION.md`](assets/artwork/classic/ATTRIBUTION.md):
  every work the plates were cut from, with its terms. Unmodified from
  Fugleramme.
- [`assets/artwork/classic/manifest.json`](assets/artwork/classic/manifest.json):
  each plate's source work and a link to the page it was cut from. Unmodified.
- [`assets/artwork/classic/geometry.json`](assets/artwork/classic/geometry.json):
  where the bird is on each plate. Unmodified.

## Data

- `assets/bird_sizes.csv`: body mass from AVONET (Tobias et al. 2022, Ecology
  Letters, [doi:10.1111/ele.13898](https://doi.org/10.1111/ele.13898)),
  CC BY 4.0, as Fugleramme ships it.
- `assets/birdnet_aliases.json`: [OpenFauna](https://github.com/tphakala/openfauna)'s
  compiled alias map, CC BY-SA 4.0, as Fugleramme ships it. See
  [`assets/ATTRIBUTION.md`](assets/ATTRIBUTION.md).
- `assets/birdnet_labels_v2.4.txt`: the BirdNET v2.4 label list (scientific and
  English names), as Fugleramme ships it. BirdNET is by the Cornell Lab of
  Ornithology and Chemnitz University of Technology.

## Label faces

The names are set in one of seven italics (Gentium Book Plus, EB Garamond,
Cormorant Garamond, Libre Baskerville, Playfair Display, Alegreya, Bitter),
with Noto faces for scripts they lack. All are under the
[SIL Open Font License 1.1](https://openfontlicense.org/). Like the plates, they
are fetched from Fugleramme's repository at the pin on first use, not shipped
here; Fugleramme's
[`assets/fonts/ATTRIBUTION.md`](https://github.com/arnegiacomo/fugleramme/blob/03046c764748a74bd6ffddf5ed526ceb0c199436/assets/fonts/ATTRIBUTION.md)
lists each family's upstream, and each family's `OFL.txt` sits beside it there.
