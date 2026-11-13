# $title

$banner

- Date: $when
- Code version: $commit
- Config hash: $config_hash
- Scenarios: $n_scenarios ($audio_hours hours of audio)

## Recall

An event counts as detected if a confirmed detection of the right class occurs between its start and its end plus 1.0 s.

$recall

## False alerts

A false alert is any alert that was shown with no matching event. Confirmed false alerts and cancelled provisional alerts are reported separately.

$false_alerts

## Latency

$latency

CPU use: $cpu

## Goals from the spec

These are goals to measure against, not claims.

$targets

## Classifier threshold sweep

$sweep

## Notes

$notes

## Scenarios

$scenarios
