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

## bts_t100_sample.json

The response of the BTS T-100 "summary by origin airport" dataset (`r495-tyji`, public domain,
https://data.bts.gov/resource/r495-tyji.json), fetched on 2026-11-04 with the same `$select`
the adapter sends. It holds ANC, BOS and SNA for 2026-04 to 2026-07, 12 rows, values
unmodified; only the whitespace is reformatted. The API returns every number as a string, and
this fixture keeps that, so the contract test fails if BTS renames a column or changes a type.

## ontime_sample.csv

The header and 8 unmodified rows of the BTS On-Time Reporting file for July 2026 (public domain,
`On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2026_7.zip` from
https://transtats.bts.gov/PREZIP/), fetched on 2026-11-05. All 110 columns are kept.

- ANC: two on-time, one late, one cancelled flight.
- BOS: one on-time, one late and one cancelled flight; the cancelled one also has a delay value,
  which the adapter must ignore.
- XNA: an airport that is not covered.

The tests zip it in memory. If BTS renames a column, this fixture is what should fail.
