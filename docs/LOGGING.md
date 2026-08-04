# Audit logging и forensic-разбор

Основной источник — `~/Library/Logs/TestFlightSlotGrabber/events.jsonl`. Каждая строка является самостоятельным JSON object и содержит:

- `timestamp`, `time_ns`, `monotonic_ns`;
- `session_id` и возрастающий `sequence`;
- `process_id`, `thread`, level и event;
- идентификаторы конкретного check/request/availability/acceptance/AX call;
- безопасные event-specific fields.

Ротация сохраняет текущий файл размером до 25 МБ и до 168 gzip-архивов `events.jsonl.N.gz`. Повторяющиеся большие headers хранятся один раз в `http_response_completed`, а связанные parser/state events ссылаются на него через `request_id`. `monitor.log` остаётся коротким человекочитаемым журналом INFO-событий.

## Что записывается

Обычный polling цикл оставляет полную цепочку:

1. `page_check_started`;
2. `http_request_started`;
3. `http_response_completed` с status, headers, range, connection reuse, attempts, redirect count, wire/decoded bytes, latency и SHA-256;
4. `page_classified` с reason и всеми parser signals;
5. `check_complete` или `state_changed`;
6. `monitor_sleep` с cycle duration, jitter, backoff, Retry-After и network failure count.

При свободном месте добавляются:

- `availability_confirmation` и отдельный cache-busted range request; событие результата содержит время старта/ответа, остаток press gate и полное detection-to-confirmation время; неоднозначный ответ автоматически повторяется полным request;
- `availability_confirmed`/`availability_rejected`;
- HTML + JSON metadata в `html-snapshots/`;
- `acceptance_pipeline_started`;
- `testflight_open_started/dispatched/completed`: отдельно видны миллисекунды отправки deep link и фоновое завершение LaunchServices;
- каждая `ax_command_started/completed`, включая arguments, exit code, parsed payload, stdout/stderr size и hash;
- `accept_pressed`, install outcome, post-accept AX tree/screenshot;
- `acceptance_pipeline_completed` или `acceptance_pipeline_failed`; failure-диагностика имеет отдельные `ax_artifacts_queued/captured/async_completed` и не задерживает повторную попытку;
- `availability_pipeline_finished` и durable state result.

При каждом новом response body hash сохраняется HTML snapshot. Поэтому стабильный `beta_full` не создаёт сотни тысяч одинаковых файлов, но любая реально новая разметка остаётся на диске.

Готовность AX проверяется helper-процессом, запущенным самим monitor/LaunchAgent: `automation_readiness`, `automation_readiness_retry` и `automation_readiness_failed`. При startup read-only событие `automation_ui_probe` дополнительно доказывает, что фоновый helper видит настоящий TestFlight UI; payload сохраняется в `state.json`. Результат, время, источник, ошибка и SHA-256 helper сохраняются в `state.json`; повторная проверка разрешения выполняется раз в минуту. Это исключает ложноположительный результат, когда ручной запуск helper наследует Accessibility от Terminal или Codex.

## Секреты

Перед JSON serialization рекурсивно редактируются поля, имена которых содержат `authorization`, `cookie`, `password`, `secret`, `token` или `chat_id`. Значение заменяется на `<redacted>`. Telegram Bot token/chat ID читаются только из Keychain и не передаются logger. HTML публичной TestFlight-страницы секретов Apple Account не содержит.

## Быстрый разбор сбоя

```bash
cd /path/to/testflight-slot-grabber
./status.sh
./diagnose.sh
tail -n 300 "$HOME/Library/Logs/TestFlightSlotGrabber/events.jsonl"
zgrep -h 'acceptance_pipeline\|availability_\|ax_command' \
  "$HOME/Library/Logs/TestFlightSlotGrabber"/events.jsonl.*.gz
```

Начинать следует с `availability_id` или `attempt_id`, затем отфильтровать все строки с этим значением. Для HTTP-сбоя используется `request_id`, для отдельного Accessibility вызова — `ax_call_id`.

Не следует публиковать diagnostic bundle без просмотра: автоматический redaction закрывает credentials, но AX tree/screenshot могут содержать видимый пользователю текст интерфейса.
