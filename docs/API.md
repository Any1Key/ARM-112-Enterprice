# API

`POST /api/login`, затем `Authorization: Bearer TOKEN`. Блокировка действует и на ранее выданный токен. Точные схемы — `/docs`.

- `GET/POST /api/scenarios`, `PUT /api/scenarios/{id}` — сценарии по роли/владельцу.
- `PUT /api/scenarios/{id}/settings` — сложность, call/dispatch, публикация, исходная карточка.
- `POST /api/runs/{scenario_id}/start`, `POST /api/runs/{id}/finish` — сессия и оценка.
- `GET /api/active-run`, `GET /api/runs/{id}` — восстановление.
- `PUT /api/runs/{id}/draft` — `{card, revision, request_id}`; устаревшая ревизия 409, повтор request_id без повторной записи.
- `POST /api/runs/{id}/register`, `/status`, `/work-call`, `/processed`, `/checked`, `/review` — регистрация, действия службы, звонки, обработка/проверка, отдельная экспертная оценка.
- `GET /api/cards`, `/api/reports`, `/api/reports/export.csv` — ограничены владельцем/ролью; CSV UTF-8 BOM с `;`.
- `GET /api/classifier`, `/api/classifier/types`, `/api/classifier/types/{id}` — версия, поиск, исторические типы.
- `POST /api/classifier/resolve` — `{classifier_ids, flags}`, расчёт обязательных служб; `/api/classifier/import` — административный импорт локального XLSX.
- `GET /api/materials`, `/api/materials/source/{manual|tickets|spec}` — задачи и исходные PDF; студенту доступна только памятка.
- `GET /api/teaching/students`, `GET/POST /api/lessons` — группа и занятия.
- `POST /api/lessons/{id}/{start|stop|next}`, `GET /api/lessons/{id}/monitor` — управление и наблюдение.
- `GET/POST /api/users`, `PUT /api/users/{id}`, `GET /api/audit` — администратор.
- `DELETE /api/users/{id}` — безвозвратное удаление пользователя и зависимых карточек/аудио; нельзя удалить себя или пользователя с активной тренировкой.
- `DELETE /api/cards/{id}` — удаление карточки, событий и связанных аудиофайлов; администратор.
- `POST /api/retention/cleanup` — ручная очистка данных старше 180 дней; администратор.
- `POST /api/telephony/account`, `POST /api/telephony/runs/{id}/call`, `GET /api/telephony/runs/{id}`, `/recording`, `/health` — локальный SIP/WebRTC, голос и запись.
- `GET/POST /api/runs/{id}/attachments`, `GET /api/attachments/{id}` — аудиовложения карточки (WAV/MP3/OGG/M4A/WEBM/FLAC, до 50 МБ) с проверкой доступа и аудитом.
- `POST /api/ai/scenarios`, `GET /api/ai/jobs/{id}` — преподаватель ставит локальную Ollama-генерацию в очередь; результат всегда черновик до проверки.
- `GET /api/operations` — администраторский монитор компонентов и свежести backup.

Сессия хранит снимок сценария/эталона. Сервер проверяет обязательные службы, ревизии, переходы статусов, владельца и ограничения активного занятия. Экспертная оценка не перезаписывает первичную.

## Данные заявителя в сценариях

Генератор сценария возвращает в `expected` поля `caller_name`, `aon`, `caller_phone` и `on_site_phone` вместе с адресом и репликой заявителя. Номера предназначены только для учебных данных и должны быть проверены преподавателем перед утверждением.

## Дополнения по новой инструкции

- `GET /api/questionnaires`, `/api/services/directory` — типизированные учебные формы и справочник служб. Ответы сохраняются в `questionnaire_answers`, число пострадавших — `victims_count`; исключение автоматически рассчитанной службы требует `service_override_reason`.
- `POST/DELETE /api/runs/{id}/supplement/lock`, `PUT /api/runs/{id}/supplement` — дополнение зарегистрированной карточки с блокировкой и ревизией; имя и статус заявителя защищены.
- `GET /api/runs/{id}/matches`, `POST /api/runs/{id}/link` — совпадения и связь с главной карточкой.
- `GET/PUT /api/operator/presence`, `POST /api/sms/incoming`, `GET /api/sms/queue`, `POST /api/sms/{id}/accept`, `GET/POST /api/runs/{id}/sms` — учебная очередь и история SMS; отправка не использует внешний шлюз.
- `POST /api/runs/{id}/reminder`, `GET /api/reminders`, `DELETE /api/runs/{id}/reminder` — напоминания.
- `POST /api/runs/{id}/help`, `GET /api/notifications`, `POST /api/notifications/{id}/ack` — запрос руководителю и подтверждение.
- `POST/GET /api/issues`, `GET /api/issues/{id}/attachment` — сообщения об ошибках, изображение по правам.
- `POST /api/telephony/account?device=hardware`, `POST /api/telephony/runs/{id}/call?device=hardware` — отдельная регистрация и входящий вызов на UDP IP-телефон.
- `POST /api/telephony/services/{101|102|103|104}/prepare`, `POST /api/telephony/runs/{id}/transfer/{extension}`, `/conference` — озвученные учебные службы, перевод и персональная конференция.

После регистрации `timer_frozen=true`: время создания больше не растёт, последующая обработка учитывается отдельно. Студенческий payload не содержит эталон опросной карты.

## Полный отчёт и подготовка звонков

- `GET /api/reports/{run_id}` — карточка, временные показатели, действия, службы и попытки звонков. Эталон и сравнения только преподавателю своей тренировки/администратору.
- `POST /api/telephony/scenarios/{scenario_id}/prepare` — подготовка озвучки доступного студенту утверждённого сценария 112.
- `POST /api/telephony/runs/{run_id}/call/cancel` — отмена подготовки/ожидания и завершение разговора.
- `GET /api/telephony/runs/{run_id}/recording?call_id=...` — запись конкретной попытки этой карточки. Без `call_id` возвращается последняя готовая запись.
- В списке звонков доступны `created_at` и `audio`: голос, движок, попадание в кэш и время подготовки.

Аппаратный профиль `POST /api/telephony/account?device=hardware` возвращает отдельный постоянный пароль из 10 строчных латинских букв и цифр. Браузерный профиль сохраняет свой прежний пароль. [Настройка аппарата в локальной сети](SIP_PHONE_SETUP.md).

Для студента `GET /api/lessons` возвращает `tasks`, `counts`, `all_completed`; статусы заданий — `pending`, `in_progress`, `skipped`, `completed`. Данные содержат только его попытки. `POST /api/lessons/{id}/tasks/{scenario_id}/start` начинает конкретное назначенное задание или возобновляет пропущенную попытку. `POST /api/lessons/{id}/next` сначала выбирает новые, затем пропущенные задания; `done=true` означает фактическое завершение всех заданий, а не только наличие попыток. Открытая карточка другого занятия блокирует новый запуск с 409.
