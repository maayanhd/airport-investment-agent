# Test fixtures

`airports_sample.csv` is a verbatim excerpt of the OurAirports `airports.csv`
(https://davidmegginson.github.io/ourairports-data/airports.csv, public domain), fetched on
2026-11-04: the header plus 7 unmodified rows chosen for their edge cases.

- ANC, BOS, BDL, JFK: covered US airports (BDL is `large_airport` in the real data).
- LHR: a non-US airport.
- Lobos Airport (Argentina) really has `iata_code` BOS, so it tests the `iso_country` filter.
- Loring Seaplane Base (US, has an IATA code) tests the airport-type filter.

To refresh, repeat the download and `grep` the same ids. If OurAirports changes its columns,
this fixture is what should make the test suite fail. Malformed-input cases are built in the
tests from this file and are clearly constructed there.
