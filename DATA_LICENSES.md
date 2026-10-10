# Data sources and licences

TwinVision Copper Pit is a fictional mine. It contains no confidential, company or personal data. This file lists every external data source used in the repository, its licence, and every synthetic parameter.

## Redistributed datasets

### GeoMet dataset (`twinvision/data/geomet/`)

| File | Rows | MD5 | Used for |
|---|---|---|---|
| `drillholes.csv` | 2,000 assay intervals | `bf48a6b1135113b6e6cf7b32f95315ad` | copper grade distribution of the block model (`Cu ppm`) |
| `comminution.csv` | 60 grindability tests | `1a33df8ba77f5d49c281ba3fff16b20b` | Bond ball mill work index distribution (from `A`, `M`, `F80`, `P80`) |
| `flotation.csv` | 53 flotation tests | `f2e90da6bfa81de1261177ee85a91570` | copper recovery (mean 87.9 %) |

- **Source:** Hoffimann, J., Augusto, J., Resende, L., Mathias, M., Mazzinghy, D., Bianchetti, M., Mendes, M., Souza, T., Andrade, V., Domingues, T., Silva, W., Silva, R., Couto, D., Fonseca, E., Gonçalves, K. (2022). *GeoMet dataset*. Zenodo, record 7051975, published 2022-03-07. <https://doi.org/10.5281/zenodo.7051975>
- **Licence:** Creative Commons Attribution 4.0 International (CC BY 4.0), <https://creativecommons.org/licenses/by/4.0/>. Redistribution is allowed with attribution.
- **Changes:** none. The files are unmodified copies of the files in that Zenodo record (checksums above).
- **How it is used:** the dataset gives the value distributions only. Each block's grade and hardness come from mapping a seeded synthetic spatial field onto these distributions (quantile mapping, see `twinvision/sim/blocks.py` and `twinvision/DECISIONS.md`). Block positions, the pit and all equipment are fictional.

## Published parameters (facts cited, no data redistributed)

| Parameter | Value | Source |
|---|---|---|
| Haul truck payload, empty mass, net power, fuel tank, top speed | 226.8 t, 159 t, 1,848 kW, 2,839 L, 60 km/h | Caterpillar 793F mining truck specification sheet (public) |
| Excavator bucket sizes | 29–34 m³ | Komatsu PC5500, Cat 6060, Liebherr R 9600 class specification sheets (public) |
| Bond's third law of comminution and the Bond ball mill work index formula | W = 10 Wi (1/√P80 − 1/√F80); Wi = 44.5 / (A^0.23 · Gbp^0.82 · (10/√P80 − 10/√F80)) × 1.1023 | Bond, F. C. (1961), *Crushing and grinding calculations*, Allis-Chalmers; standard mineral processing textbooks |
| Diesel brake-specific fuel consumption | 0.25 L/kWh | typical for large mining diesel engines |

## Synthetic parameters (labelled "synthetic" or "site" in `twinvision/sim/params.py`)

All of the following are invented for the fictional site and justified in `twinvision/DECISIONS.md`: pit geometry and road network, block cluster positions and grade domains, fleet size, dispatcher targets, stockpile capacities and opening stocks, plant target rate and grade band, crusher size, fuel bay size and delivery rate, failure and maintenance parameters (wear rates, hazard model, MTTR, PM duration), grid cap default, simulation clock and random seed.

## Platform

The OpenEgiz platform files in this repository keep their original licences (see `LICENSE` and the files under `deploy/` and `examples/`).
