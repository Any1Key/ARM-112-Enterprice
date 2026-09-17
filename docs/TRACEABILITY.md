# Матрица соответствия ТЗ и памятке

Разбор источников: [SOURCE_REVIEW.md](SOURCE_REVIEW.md). Статусы относятся к учебной системе.

| Требование | Реализовано | Остаток |
|---|---|---|
| Локальный web / PostgreSQL | Docker, рабочая БД | Испытания целевого Ubuntu/Astra |
| Роли и управление пользователями | Владение, блокировка, смена роли | Корпоративная учётная система |
| Два режима обучения | Карточка 112 и действия ДДС | Все интеграционные особенности ПОВ-112 |
| Сценарии | Черновик, сложность, эталон, утверждение; ограничения активных занятий | Мультимедийные ветвления |
| Занятия | Назначение группе, старт/стоп, мониторинг | Дополнительная групповая аналитика |
| Карточка, памятка стр. 12–18 | Адрес, 3 телефона, заявитель, ЕКП, анкета, оповещение | Адресный справочник и полигоны |
| Вложения карточки | Локальная загрузка аудио к учебной карточке, скачивание по правам и аудит | Политика долгосрочного хранения медиа |
| XLSX | 1 283 типа, 24 группы, условные службы, версии | Региональная верификация правил |
| Билеты | 96 задач с происхождением, импорт в сценарий | Проверка OCR и эталонов преподавателем |
| ДДС, памятка стр. 19–24 | Статусы, комментарии, история, рабочие звонки, особенность 103 | Настоящие интеграционные сообщения |
| Поиск, памятка стр. 35+ | Scoped search, filters, pagination and source-style journal columns | Архивное хранение вне текущей БД |
| Время | По умолчанию 30 с, настройка; ДДС по первому ответу | Полный протокол испытаний |
| Оценка 0–100 | Правиловая + local SentenceTransformer evidence, grammar checks, errors/time, expert review and audit | Calibration on domain-labelled answers |
| Отчёты | Свои/преподавательские результаты, CSV | Корпоративные форматы |
| Восстановление | Черновики, ревизии, повторная отправка, повторный вход | Испытания 30-секундного разрыва/аварий |
| Учебный вызов | UI simulation and local Asterisk SIP/WebRTC, Russian voice, WSS, TURN and recording | Full outage test and production telephony integration |
| Локальные SIP / AI | Asterisk + four Piper Russian voices selected by caller name, with random choice among matching voices + Ollama draft generation for teacher approval | Domain validation of generated scenarios |
| 100 пользователей / 20 сессий | Не подтверждено | Нагрузочные испытания с VoIP |
| TLS | Caddy internal TLS and SIP/WSS inside contour | TLS to PostgreSQL/Redis and corporate PKI |
| Хранение >= 6 месяцев | Карточки, результаты, аудио и аудит хранятся 180 дней; ежедневная очистка и ручной запуск | Внешний архив и юридически утверждённая политика |
| Ежедневный backup | Scheduled custom-format dump, freshness monitor, pg_restore-list validation | External/off-host retention and restore drill |
| Мониторинг / HA | Admin component monitor for DB/Redis/Asterisk/voice/ML/grammar/Ollama/backup | Metrics, alerting and multi-node HA |

Redis сам по себе не обеспечивает отказоустойчивость. Дополнительные таблицы создаются без удаления данных; промышленное управление схемой требует миграций.
