# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from types import SimpleNamespace

import pytest

from fsq_agent.core.interfaces import HarnessFactory
from fsq_agent.models import HarnessSettings


class Resource:
    def __init__(self, failure=None):
        self.calls = 0
        self.failure = failure

    def close(self):
        self.calls += 1
        if self.failure is not None:
            raise self.failure


@pytest.mark.parametrize("platform", ["android", "web", "windows", "macos"])
def test_harness_disposes_all_owned_resources_once(platform):
    driver, evaluator = Resource(), Resource()
    factory = SimpleNamespace(**{f"create_{platform}_driver": lambda *args, **kwargs: driver})
    harness = HarnessFactory(driver_factory=factory).create_harness(platform=platform, harness_settings=HarnessSettings(), ai_assertion_evaluator=evaluator)
    harness.close()
    harness.close()
    assert driver.calls == evaluator.calls == 1


def test_harness_disposal_retries_only_failed_resource():
    primary = OSError("driver close")
    driver, evaluator = Resource(primary), Resource()
    harness = HarnessFactory(driver_factory=SimpleNamespace(create_web_driver=lambda *_: driver)).create_harness(platform="web", harness_settings=HarnessSettings(), ai_assertion_evaluator=evaluator)
    with pytest.raises(OSError, match="driver close") as failure:
        harness.close()
    assert failure.value is primary
    assert driver.calls == evaluator.calls == 1
    driver.failure = None
    harness.close()
    assert driver.calls == 2
    assert evaluator.calls == 1


@pytest.mark.parametrize("during_driver", [False, True])
def test_harness_construction_failure_disposes_resources_without_masking(monkeypatch, during_driver):
    primary = RuntimeError("construction failed")
    driver, evaluator = Resource(OSError("cleanup failed")), Resource()

    def fail(*args, **kwargs):
        raise primary

    monkeypatch.setattr("fsq_agent.harnesses._factory.WebHarness", fail)
    factory = SimpleNamespace(create_web_driver=fail if during_driver else lambda *_: driver)
    with pytest.raises(RuntimeError) as failure:
        HarnessFactory(driver_factory=factory).create_harness(platform="web", harness_settings=HarnessSettings(), ai_assertion_evaluator=evaluator)
    assert failure.value is primary
    assert driver.calls == (0 if during_driver else 1)
    assert evaluator.calls == 1
