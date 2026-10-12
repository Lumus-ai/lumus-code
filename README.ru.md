# Lumus Code

ИИ-агент для терминала: комнаты для совместной работы, команда ИИ на проект, заметки, Hub с CLI-инструментами, тема в стиле Gemini и **режим внешнего агента**, который позволяет безопасно подключить стороннего агента к комнате.

Основан на OpenCode и NeiP4n/opencode-plus-plus (лицензия MIT). Проект не создан командой OpenCode и не связан с ней.

## Установка

Linux / macOS:

    curl -fsSL https://raw.githubusercontent.com/Lumus-ai/lumus-code/main/lumus-code-install.sh | sh

Windows (PowerShell):

    irm https://raw.githubusercontent.com/Lumus-ai/lumus-code/main/lumus-code-install.ps1 | iex

Запуск: `lumus-code`. Обновление: выполнить команду установки ещё раз. Установщик для Windows не проверялся на чистой машине.

## Комнаты

Хост делится сессией или проектом (кнопка Host), гость подключается по коду или адресу (Connect). Роли: viewer, member, helper, cohost.

## Внешний агент

- Агент входит в комнату с `external: true` и получает не выше роли member.
- `answer` и `tool_result` только пишут в чат комнаты, модель хоста не запускают.
- `command`, `edit` и `prompt` всегда ждут разрешения хоста. В Host есть настройка «Доверять Instinct (внешний агент)»: промпты идут без вопроса (по умолчанию выключено, сбрасывается при новом коде входа; команды и правки всё равно спрашивают).
- Строки, начатые с `@instinct` или командой `/instinct`, уходят агенту в чат без запуска модели.
- Клиент: `script/lumus-guest.py`. Подробности и протокол: [README.md](README.md).

## xKiro

Встроенный провайдер xKiro: в окне Connect выбрать xKiro и вставить ключ (или переменная `XKIRO_API_KEY`). Все модели из `GET https://api.xkiro.com/v1/models` появятся в выборе модели с пометкой `[free]`, `[paid]` или `[premium]`. Проверено юнит-тестами; с настоящим ключом не запускалось.

## Если порт занят

У Lumus Code свои порты (47474/47475) и свои папки (`~/.config/lumus-code`, `~/.local/state/lumus-code`). Сменить порт: `lumus-code service set port 51234`.

## Лицензия

MIT, исходное уведомление об авторских правах сохранено в [LICENSE](LICENSE).
