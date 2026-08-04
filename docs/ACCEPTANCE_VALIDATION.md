# Валидация автоматического принятия

Проверено 4 августа 2026 года на этом Mac.

## Подтверждено на реальных сервисах и UI

- Telegram public page корректно определяется как `beta_full`.
- TestFlight открывает Telegram через `itms-beta://testflight.apple.com/join/u6iogfd0`.
- Настоящий Telegram AX tree виден helper; текущий статус — `beta_full`, сборка iOS-only и несовместима с установкой на Mac.
- Проверка выполнена именно из LaunchAgent после выдачи новой подписи: `ready_to_accept=true`; read-only startup probe вернул `app_visible=true`, `beta_full=true`, 61 AX-элемент и отсутствие Accept. Tree и screenshot сохранены только локально и в репозиторий не входят.
- Доступная публичная бета Kashikan (`Ssjq4DbX`) реально определилась как `available`.
- В живой карточке Kashikan кнопка имеет description `Принять` и identifier `TestFlight.offerButton.accept`; это тот identifier, который production helper разрешает нажать для Telegram.
- 4 августа 2026 в 11:55:48 МСК Telegram public page реально перешла в `available`; второй независимый HTTP-ответ также был `available`, после чего production pipeline стартовал с `dry_run=false`. Старая синхронная отправка deep link заняла 4,976 с, и к началу AX TestFlight уже показывал `beta_full`.
- Исправленный путь проверен на настоящем TestFlight при текущем `beta_full`: Python передал deep link за 2,377 мс, AX command стартовал на следующей миллисекунде, `/usr/bin/open` завершился фоново через 84 мс. Failure-result сформирован через 3,574 с, а tree/PNG и notification не блокировали pipeline.
- Та же проверка выполнена отдельной временной launchd-задачей: dispatch 3,077 мс, AX стартовал в ту же миллисекунду, exit 16 получен через 3,652 с, асинхронные артефакты завершились ещё через 480 мс. Это подтверждает ускорение именно для фонового TCC-контекста production-agent.

## Подтверждено end-to-end в безопасной изоляции

`scripts/test-full-pipeline.sh` использует отдельные временные data/log directories и локальное TestFlight-подобное окно. Проверена вся управляющая цепочка без подмены методов:

1. monitor получает fixture `available`;
2. выполняет вторую confirmation-проверку;
3. запускает acceptance pipeline;
4. запускает mock app через LaunchServices;
5. нативный helper рекурсивно находит кнопку и выполняет настоящий Accessibility `AXPress`;
6. helper подтверждает исчезновение Accept и появление Install;
7. выполняет `AXPress Install` и подтверждает Open;
8. monitor durable-сохраняет `accepted=true` только после успешного перехода;
9. сохраняются audit events, HTML snapshots, AX tree и screenshot.

Прямой прогон: success, 56 audit events. Воспроизводимый `scripts/test-launch-agent-pipeline.sh` из настоящего launchd-контекста также завершился success (`accepted=true`, 56 событий); так проверена именно та TCC-атрибуция, с которой работает production monitor. Валидатор подтвердил непрерывную sequence, один session, сквозные availability/attempt/AX/notification IDs и отсутствие неотредактированных sensitive fields. После подтверждения `available` pipeline стартовал за миллисекунды; предварительные уведомления выполнялись асинхронно и не задерживали AX-вызов.

## Что пока не заявляется

Реальный `AXPress` на чужой доступной публичной бете не выполнялся. Такое действие добавляет Apple Account пользователя в чужую группу, занимает настоящий tester slot и является внешним изменением состояния. Без явного разрешения проект ограничился чтением живого AX tree.

Во втором реальном событии исправленный monitor отправил deep link через 29 мс после локальной классификации, однако TestFlight не показал Accept, пока публичный HTML оставался `available` около 10 секунд. Внешний checker увидел событие примерно на 50 секунд раньше локального регионального endpoint. После объявления о распределении Telegram‑приглашений через `@TestFlightInvitesBot` проект не заявляет способность получить Telegram beta access. Подробности находятся в [`TELEGRAM_POSTMORTEM.md`](TELEGRAM_POSTMORTEM.md).
