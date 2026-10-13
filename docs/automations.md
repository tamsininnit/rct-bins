# Automation examples

These examples assume a property named **Home**. Replace `notify.notify` with your own
notification action, such as `notify.mobile_app_<your_phone>` or a notify group. It is a placeholder.

## Evening before, naming the bins

```yaml
alias: Bins - evening reminder
triggers:
  - trigger: time
    at: "18:00:00"
conditions:
  - condition: state
    entity_id: binary_sensor.rct_bins_home_collection_tomorrow
    state: "on"
actions:
  - action: notify.notify  # placeholder
    data:
      title: Bins tomorrow
      message: >-
        Put out: {{ state_attr('binary_sensor.rct_bins_home_collection_tomorrow',
        'collection_types') | join(', ') }}.
mode: single
```

## Morning of

```yaml
alias: Bins - morning reminder
triggers:
  - trigger: time
    at: "07:00:00"
conditions:
  - condition: state
    entity_id: binary_sensor.rct_bins_home_collection_today
    state: "on"
actions:
  - action: notify.notify  # placeholder
    data:
      title: Bin day
      message: >-
        Collections today: {{ state_attr('binary_sensor.rct_bins_home_collection_today',
        'collection_types') | join(', ') }}.
mode: single
```

## Different messages for different waste types

In the schedules observed during development, black bags were collected every three weeks, so
they're easy to forget. This sends a
louder reminder on black-bag weeks.

```yaml
alias: Bins - black bag week
triggers:
  - trigger: time
    at: "18:00:00"
conditions:
  - condition: state
    entity_id: binary_sensor.rct_bins_home_collection_tomorrow
    state: "on"
actions:
  - variables:
      types: "{{ state_attr('binary_sensor.rct_bins_home_collection_tomorrow', 'collection_types') }}"
  - choose:
      - conditions: "{{ 'General Waste' in types }}"
        sequence:
          - action: notify.notify  # placeholder
            data:
              title: "Black bag week!"
              message: >-
                Black bags go out tonight, plus
                {{ types | reject('eq', 'General Waste') | join(', ') }}.
      - conditions: "{{ 'Garden Waste' in types }}"
        sequence:
          - action: notify.notify  # placeholder
            data:
              title: Bins tomorrow
              message: "Garden waste is due too: {{ types | join(', ') }}."
    default:
      - action: notify.notify  # placeholder
        data:
          title: Bins tomorrow
          message: "{{ types | join(', ') }}"
mode: single
```

The values in `collection_types` are the normalised display names: `General Waste`, `Recycling`,
`Food Waste`, `Garden Waste` and `Hygiene Waste`. Any type the integration doesn't recognise
appears under the council's own label.

## Using the calendar trigger instead

Calendar triggers fire once per event, so this sends one message per waste type, at 6 PM the day
before. The offset is measured from the start of the all-day event, which is midnight local time.

```yaml
alias: Bins - calendar reminder
triggers:
  - trigger: calendar
    event: start
    entity_id: calendar.rct_bins_home
    offset: "-06:00:00"
actions:
  - action: notify.notify  # placeholder
    data:
      title: Bins tomorrow
      message: "{{ trigger.calendar_event.summary }}"
mode: queued
```

## A reminder that you haven't put the bins out

This example pairs the reminder with a door sensor. The `binary_sensor.side_gate` entity is an
example only.

```yaml
alias: Bins - nag if gate not opened
triggers:
  - trigger: time
    at: "21:30:00"
conditions:
  - condition: state
    entity_id: binary_sensor.rct_bins_home_collection_tomorrow
    state: "on"
  - condition: state
    entity_id: binary_sensor.side_gate  # example entity
    state: "off"
    for: "03:30:00"
actions:
  - action: notify.notify  # placeholder
    data:
      message: "Bins haven't gone out yet."
mode: single
```

## Notes

- The reminder binary sensors change at **local midnight** even if Home Assistant hasn't contacted the
  council. Time-based automations always see the correct day.
- If the council hasn't published far enough ahead, `collection_tomorrow` can be `unknown`. The
  `state: "on"` conditions above simply don't fire in that case.
