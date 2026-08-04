# TestFlight Slot Grabber

![macOS 13+](https://img.shields.io/badge/macOS-13%2B-black)
![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-blue)
![Swift 5.9](https://img.shields.io/badge/Swift-5.9-orange)

Локальный исследовательский инструмент для мониторинга публичных страниц TestFlight и безопасной автоматизации интерфейса TestFlight на macOS. Python‑монитор классифицирует состояние приглашения, а нативный Swift‑helper через Accessibility находит точную кнопку `Accept` и выполняет `AXPress`.

> [!IMPORTANT]
> **Для актуальной Telegram beta этот проект не получает доступ к тестированию.** Согласно опубликованному в сообществе объявлению, приглашения теперь распределяет `@TestFlightInvitesBot` среди подходящих активных участников группы. Скорость открытия публичной ссылки не заменяет одобрение бота и не создаёт право на слот. Проект сохранён как воспроизводимый технический эксперимент и может быть полезен для других действительно публичных first‑come TestFlight‑групп.

## Что умеет проект

- различать `beta_full`, `available`, `invitation_invalid`, `build_unavailable`, `rate_limited`, `network_error` и неизвестную разметку;
- подтверждать `available` вторым независимым запросом перед любым действием;
- открывать точный `itms-beta://` deep link без ожидания завершения LaunchServices;
- проверять заголовок целевого приложения и нажимать только подтверждённый AX identifier `TestFlight.offerButton.accept`;
- при необходимости нажимать `Install`/`Update` и подтверждать переход к `Open`;
- запускать полный поток на локальном mock‑окне без занятия настоящего tester slot;
- сохранять JSONL audit log, HTML snapshots, AX tree и screenshot ошибки;
- отправлять локальные уведомления и необязательные Telegram‑уведомления через секреты из Keychain;
- работать как LaunchAgent после входа пользователя.

## Чего проект не умеет

- получать eligibility или персональное приглашение вместо владельца beta‑программы;
- обходить списки допуска, лимиты Apple, очередь бота или привязку приглашения к Apple Account;
- гарантировать, что публичная HTML‑страница синхронизирована с авторизованным backend TestFlight;
- безопасно воспроизводить закрытый API принятия — экспериментальный replay намеренно выключен;
- устанавливать iOS‑only сборку на Mac.

## Почему Telegram больше не является рабочим сценарием

В ходе эксперимента публичная страница Telegram действительно дважды показывала `available`, и pipeline запускался автоматически. Однако TestFlight для авторизованного аккаунта в те же моменты показывал `beta_full` и не создавал кнопку Accept.

Во втором наблюдении внешний публичный checker увидел изменение примерно на 50 секунд раньше локального endpoint. Локальный монитор всё это время получал свежие по `Date` ответы `beta_full` из одного регионального кластера Apple; первый локальный `available` пришёл только в `12:55:53.691`, а deep link был отправлен в `12:55:53.728` — через 37 мс. Следовательно, увеличение частоты локального polling не устраняет региональную рассинхронизацию публичной страницы.

Итог: публичный HTML — лишь сигнал, а решение о допуске принимает account‑scoped backend TestFlight или владелец программы. Подробности: [постмортем Telegram](docs/TELEGRAM_POSTMORTEM.md).

## Архитектура

```text
public TestFlight page
        │
        ▼
HTTP classifier ── inconclusive ──► full HTML retry
        │ available
        ├──────────────► non-blocking deep-link dispatch
        ▼
independent confirmation + 300 ms safety gate
        │ confirmed
        ▼
Swift AX helper ──► exact app title ──► exact Accept identifier
        │
        ├─ success ─► optional Install/Update ─► durable state
        └─ failure ─► AX tree + screenshot + notification
```

Основной процесс строго последовательный: десятки параллельных запросов не создаются. При `429`, сетевой ошибке или неизвестном ответе включается экспоненциальный backoff с поддержкой `Retry-After`.

## Требования

- macOS 13 или новее;
- Python 3.9+ без сторонних production‑зависимостей;
- Swift 5.9 / Xcode Command Line Tools;
- TestFlight из Mac App Store;
- Accessibility permission для собранного helper app;
- Screen Recording необязателен и нужен только для screenshot диагностики.

Проверено на Apple Silicon (MacBook Air M3). Swift package не содержит arm64‑специфичного кода и собирается под архитектуру текущего Mac.

## Быстрый старт

```bash
git clone https://github.com/rub1kub/testflight-slot-grabber.git
cd testflight-slot-grabber
./setup.sh
```

`setup.sh` создаст локальный `config.json` из `config.example.json`, соберёт Swift‑helper и mock app, затем запустит unit tests. Локальный конфиг исключён из Git.

По умолчанию включён безопасный режим:

```json
"dry_run": true
```

Измените в `config.json` три согласованных значения:

```json
{
  "target_url": "https://testflight.apple.com/join/JOIN_CODE",
  "join_code": "JOIN_CODE",
  "expected_app_name": "Exact App Name",
  "automation": {
    "deep_link": "itms-beta://testflight.apple.com/join/JOIN_CODE"
  }
}
```

Затем выполните безопасную проверку:

```bash
./run.sh check
./run.sh accept --dry-run
./scripts/test-full-pipeline.sh
```

Только после успешного mock‑теста и проверки правильного приложения можно осознанно изменить `dry_run` на `false`.

## Разрешение Accessibility

```bash
./scripts/request-accessibility.sh
```

Откройте `System Settings → Privacy & Security → Accessibility` и включите:

```text
<repository>/helper/TestFlightAXHelper.app
```

Проверка:

```bash
./helper/TestFlightAXHelper.app/Contents/MacOS/testflight-ax permission --json
./scripts/test-mock-flow.sh
```

После изменения и пересборки Swift‑бинарника macOS может потребовать выдать разрешение новой code signature. Повторная сборка неизменившегося helper сохраняет существующую подпись.

## Команды

```bash
./run.sh                              # постоянный monitor
./run.sh check                        # один HTTP-check
./run.sh accept --dry-run             # открыть pipeline без кликов
./run.sh diagnose                     # окружение и readiness
./status.sh                           # health + LaunchAgent status
./stop.sh                             # остановить monitor

./scripts/test-full-pipeline.sh        # fixture → real AXPress в mock UI
./scripts/test-launch-agent-pipeline.sh
./scripts/test-mock-flow.sh
```

Эквивалентный Python CLI:

```bash
python3 -m testflight_grabber monitor
python3 -m testflight_grabber check
python3 -m testflight_grabber accept --dry-run
python3 -m testflight_grabber diagnose
python3 -m testflight_grabber health
```

Нативный AX CLI:

```bash
AX="./helper/TestFlightAXHelper.app/Contents/MacOS/testflight-ax"
"$AX" inspect --json --bundle-id com.apple.TestFlight --app-name "Exact App Name"
"$AX" status  --json --bundle-id com.apple.TestFlight --app-name "Exact App Name"
"$AX" accept  --json --bundle-id com.apple.TestFlight --app-name "Exact App Name" --timeout 12
"$AX" install --json --bundle-id com.apple.TestFlight --app-name "Exact App Name" --timeout 30
```

Helper отказывается работать с любым production‑процессом, кроме `com.apple.TestFlight`. Для Accept он требует одновременно точный заголовок приложения и identifier `TestFlight.offerButton.accept`; локализованный текст кнопки сам по себе недостаточен.

## Настройка polling

Публичный конфиг использует консервативные `7 ± 1,5` секунды. Интервал можно переопределить локально:

```bash
TESTFLIGHT_INTERVAL_SECONDS=5 TESTFLIGHT_JITTER_SECONDS=0.5 ./run.sh
```

Apple не публикует rate limit для `testflight.apple.com/join/*`. Измерения коротких серий без `429` не доказывают безопасный долговременный предел. Значения ниже секунды также не гарантируют более ранний сигнал из‑за CDN/региональной рассинхронизации. См. [результаты измерений](docs/RATE_LIMIT_FINDINGS.md).

## Автозапуск

```bash
./scripts/install-launch-agent.sh
./status.sh
./scripts/uninstall-launch-agent.sh
```

LaunchAgent запускается после входа, перезапускается после аварийного выхода и не допускает второй экземпляр через lock file.

## Логи и состояние

```text
~/Library/Logs/TestFlightSlotGrabber/monitor.log
~/Library/Logs/TestFlightSlotGrabber/events.jsonl
~/Library/Logs/TestFlightSlotGrabber/html-snapshots/
~/Library/Logs/TestFlightSlotGrabber/artifacts/
~/Library/Application Support/TestFlightSlotGrabber/state.json
```

JSONL содержит wall/monotonic time, последовательный номер события, request/check/attempt IDs, HTTP metadata, parser signals и AX payload. Поля cookies, authorization, passwords, tokens и chat IDs рекурсивно заменяются на `<redacted>`. Перед публикацией screenshot или AX tree всё равно следует просмотреть вручную. Подробнее: [формат логов](docs/LOGGING.md).

## Проверки

```bash
python3 -m unittest discover -s tests -v
swift build -c release
./helper/TestFlightAXHelper.app/Contents/MacOS/testflight-ax self-test --json
./scripts/test-full-pipeline.sh
```

Локально подтверждены:

- 32 Python unit tests для parser, config, redaction, lock/state и trigger pipeline;
- Swift self‑test;
- полный fixture `available → confirmation → AXPress Accept → Install → Open` на mock UI;
- чтение реального TestFlight AX tree из LaunchAgent;
- реальные identifiers `TestFlight.offerButton.accept` и `TestFlight.offerButton.install` на доступных сторонних beta‑карточках без нажатия Accept;
- автоматический запуск production pipeline при двух реальных переходах Telegram public page в `available`;
- отсутствие безопасно доступного API replay без запрещённого TLS/pinning bypass.

## Репозиторий

```text
testflight_grabber/   Python monitor, parser, pipeline, logging
Sources/              Swift AX helper и mock UI
tests/                unit tests и HTML fixtures
scripts/              setup, LaunchAgent, mock и iPhone tooling
launchd/              plist template
docs/                 протоколы экспериментов и ограничения
config.example.json   безопасный публичный конфиг
```

Собранные `.app`, `.build`, локальный `config.json`, логи, state и Keychain‑данные в репозиторий не входят.

## Удаление

```bash
./scripts/uninstall-launch-agent.sh
```

После этого каталог репозитория можно удалить. При необходимости отдельно удаляются `~/Library/Logs/TestFlightSlotGrabber` и `~/Library/Application Support/TestFlightSlotGrabber`. Проект не изменяет SIP, Apple Account, системные сертификаты или настройки TestFlight.

## Ответственное использование

Используйте инструмент только со своим Mac, своим Apple Account и публичными приглашениями. Не обходите eligibility, device binding, certificate pinning или ограничения владельца beta‑программы. Частый polling создаёт нагрузку и не даёт преимущество, если допуск распределяется владельцем программы или account‑scoped backend.
