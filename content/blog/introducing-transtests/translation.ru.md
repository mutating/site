---
title: "Представляем transtests: тестирование трёх видов Python-функций"
description: "Запускает одну pytest-проверку поведения для обычной, асинхронной и генераторной форм одного шаблона Python-функции."
---

Python-инструментам, которые анализируют, оборачивают или преобразуют callables, обычно нужно поддерживать несколько видов функций. Декоратор может работать с обычной функцией, но захватывать блокировку не в тот момент, пропускать instrumentation или иначе обрабатывать исключения, когда выполнение откладывается до `await` корутины или итерации генератора. Если копировать один поведенческий тест трижды, эти сценарии легко начать поддерживать по-разному.

[`pytest.mark.parametrize`](https://docs.pytest.org/en/stable/how-to/parametrize.html) может запустить несколько случаев, но проекту всё равно нужно создать каждую форму callable и правильно её вызвать. [pytest-asyncio](https://pytest-asyncio.readthedocs.io/) и [AnyIO](https://anyio.readthedocs.io/en/stable/testing.html) выполняют асинхронные тесты, но не создают синхронный, coroutine- и generator-варианты из одного исходного шаблона. Отдельный fixture нужен, чтобы проверять одно поведение во всех трёх формах без дублирования логики преобразования и вызова.

[transtests](https://github.com/mutating/transtests) — небольшая библиотека тестирования и pytest-плагин для запуска одного шаблона функции в синхронной, асинхронной и генераторной формах. Он предоставляет эту проверку совместимости как fixture с тремя отдельно отображаемыми случаями. [Релиз](https://github.com/mutating/transtests/releases/tag/0.0.1) доступен на GitHub.

## Параметризация по типу функции

После установки pytest автоматически обнаруживает fixture `transformed`. Любой запросивший его тест превращается в три отдельно отображаемых случая: `sync`, `async` и `generator`.

```python
from asyncio import run
from inspect import iscoroutinefunction, isgeneratorfunction

def test_addition(transformed):
    @transformed
    def add(left, right):
        return left + right

    if iscoroutinefunction(add):
        assert run(add(1, 2)) == 3
    elif isgeneratorfunction(add):
        assert list(add(1, 2)) == [3]
    else:
        assert add(1, 2) == 3
```

Pytest запускает этот тест трижды и отдельно отображает каждый вид функции.

Fixture возвращает декоратор. В синхронном случае он отдаёт функцию-шаблон. В остальных случаях он просит [transfunctions](https://github.com/mutating/transfunctions) построить coroutine- и generator-oriented варианты из того же исходного шаблона. Затем тест вызывает результат подходящим способом: напрямую, через event loop или итерацией.

Общее поведение и assertions остаются в одном месте, а обычная изоляция pytest и ID отдельных случаев сохраняются. Специфический для вида функции синтаксис можно поместить в markers `sync_context`, `async_context` и `generator_context` из transfunctions.

У генераторного режима есть важное ограничение: он преобразует явные markers вроде `yield_from_it`, но не превращает каждый обычный `return` в `yield`. Поэтому в шаблоне должна быть настоящая генераторная точка. Асинхронные генераторы не поддерживаются.

## Сравнение с похожими инструментами

[`pytest.mark.parametrize`](https://docs.pytest.org/en/stable/how-to/parametrize.html) способен воспроизвести три случая, если проект сам реализует фабрику преобразований и правила вызова. Он универсальнее и лучше, когда нужно проверять независимо написанные sync- и async-API, а не сгенерированные варианты.

[pytest-asyncio](https://pytest-asyncio.readthedocs.io/) позволяет pytest выполнять coroutine-тесты и управлять asyncio-fixtures. [Pytest-плагин AnyIO](https://anyio.readthedocs.io/en/stable/testing.html) запускает async-тесты на asyncio и Trio. Они меняют runtime асинхронного теста, а transtests — тип callable, переданного тестируемому коду. Эти инструменты дополняют друг друга.

[Hypothesis](https://hypothesis.readthedocs.io/) меняет входные данные, а transtests — вид тестируемого callable: обычный, асинхронный или генераторный. [unasync](https://github.com/python-trio/unasync) преобразует целые деревья исходников и лучше подходит для поддержки отдельных sync- и async-API.

## Для кого предназначен transtests

Самые очевидные пользователи — авторы декораторов, реестров callbacks, dispatchers, middleware, instrumentation и metaprogramming-инструментов. Таким библиотекам часто важно не только что вернул callback, но и когда его тело действительно исполнилось.

Вместе с инструментом вроде [`LockTraceWrapper`](https://pypi.org/project/locklib/) один общий контракт может проверить, что под lock выполнилось именно тело callback, а не только создание coroutine- или generator-объекта. В более крупном наборе тестов transtests обеспечивает эту проверку совместимости, не определяя устройство остальной тестовой архитектуры.

## Что дальше

Проект будет упрощать диагностику и единообразное использование трёх режимов в разных асинхронных тестовых окружениях. Документация и интеграция улучшатся, а библиотека останется сосредоточена на проверке общего поведения нескольких видов Python-функций.
