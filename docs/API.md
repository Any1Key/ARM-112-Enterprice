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
