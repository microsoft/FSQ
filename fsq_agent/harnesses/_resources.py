# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import logging

from fsq_agent.core.interfaces import AIAssertionEvaluatorProtocol, DriverObservationInterface


class OwnedResources:
    def __init__(self, *resources: DriverObservationInterface | AIAssertionEvaluatorProtocol | None) -> None:
        self._pending = {id(resource): resource for resource in resources if resource is not None}

    def close(self) -> None:
        failure = None
        for identity, resource in tuple(self._pending.items()):
            try:
                resource.close()
            except BaseException as exc:  # noqa: BLE001 - one failure must not skip remaining resources.
                if failure is None:
                    failure = exc
                else:
                    logging.getLogger(__name__).warning("Additional resource disposal failed (%s)", type(exc).__name__)
            else:
                del self._pending[identity]
        if failure is not None:
            raise failure
