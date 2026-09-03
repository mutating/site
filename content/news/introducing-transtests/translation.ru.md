---
title: "Представляем transtests: один тест для обычных, асинхронных и генераторных функций"
description: "Запускает одну pytest-проверку поведения для обычной, асинхронной и генераторной форм одного шаблона Python-функции."
---

Python-инструментам, которые оборачивают или преобразуют callables, часто нужно поддерживать обычные функции, корутины и генераторы. Декоратор может работать синхронно, но вести себя иначе, когда выполнение ждёт `await` или итерации. Если копировать один тест трижды, эти случаи легко начать поддерживать по-разному.

[`pytest.mark.parametrize`](https://docs.pytest.org/en/stable/how-to/parametrize.html) запускает несколько случаев, а [pytest-asyncio](https://pytest-asyncio.readthedocs.io/) и [AnyIO](https://anyio.readthedocs.io/en/stable/testing.html) — асинхронные тесты. Но ни один из них не создаёт и не вызывает три вида callable из одного исходного шаблона.

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

Fixture возвращает декоратор. Для асинхронного и генераторного случаев [transfunctions](https://github.com/mutating/transfunctions) строит варианты из того же шаблона. Общие assertions остаются в одном тесте, а pytest сохраняет отдельные ID случаев.

У генераторного режима есть важное ограничение: он преобразует явные markers вроде `yield_from_it`, но не превращает каждый обычный `return` в `yield`. Поэтому в шаблоне должна быть настоящая генераторная точка. Асинхронные генераторы не поддерживаются.

## Сравнение с похожими инструментами

Обычная параметризация pytest лучше, когда sync- и async-API реализованы независимо, а не созданы из одного шаблона.

[pytest-asyncio](https://pytest-asyncio.readthedocs.io/) и [pytest-плагин AnyIO](https://anyio.readthedocs.io/en/stable/testing.html) меняют async-runtime, а transtests — вид callable. Эти инструменты дополняют друг друга.

[Hypothesis](https://hypothesis.readthedocs.io/) меняет входные данные, а transtests — вид тестируемого callable: обычный, асинхронный или генераторный. [unasync](https://github.com/python-trio/unasync) преобразует целые деревья исходников и лучше подходит для поддержки отдельных sync- и async-API.

## Для кого предназначен transtests

Самые очевидные пользователи — авторы декораторов, реестров callbacks, dispatchers, middleware, instrumentation и metaprogramming-инструментов. Таким библиотекам часто важно не только что вернул callback, но и когда его тело действительно исполнилось.

Вместе с [`LockTraceWrapper`](https://pypi.org/project/locklib/) один тест может проверить, что под lock выполнилось тело callback, а не только создание coroutine- или generator-объекта. Такая проверка встраивается в более крупный набор тестов, не определяя его архитектуру.
