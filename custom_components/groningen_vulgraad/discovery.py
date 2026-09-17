"""Find the portal's operation identifiers instead of hardcoding them.

Every call to the portal must name an `operationId`: an opaque handle compiled
into the app when it is built. They are regenerated on a redeploy, which used
to break this integration until someone shipped a new version.

They do not have to be guessed. The portal serves its own page definitions over
plain anonymous HTTP, and each definition names the operation behind every
widget, right next to something that describes it in model terms:

    "datasource":{"type":"microflow","path":"Burger_Applicatie.link",
                  "operationId":"..."}

So an operation can be found by what it *does* rather than by its identifier.
The names used as anchors here are entity names in the portal's data model,
which change far less often than the build does: a rebuild regenerates every
id, while `Burger_Applicatie.Inzamelplek` stays put.

Which page to read is never guessed either. The bootstrap response names the
landing page, and every step afterwards names the page it opens next, so the
portal walks us through its own flow.
"""

from __future__ import annotations

import logging
import re

_LOGGER = logging.getLogger(__name__)

# A window big enough to hold one widget's datasource or action block.
_WINDOW = 600

# Where one widget's definition stops and the next begins. A match is cut at
# the first of these, so a short block can never borrow the identifier of the
# widget that follows it.
_BOUNDARIES = (
    '"datasource":{',
    '"type":"microflowCall"',
    '"argMap":{',
    '"$widgetId"',
)


class PageDefinition:
    """One page definition, queried by what an operation does."""

    def __init__(self, path: str, text: str) -> None:
        self.path = path
        self._text = text

    # -- datasources

    def microflow_datasource(self, entity: str) -> str | None:
        """The operation behind a widget showing `entity` from a microflow.

        Finds the seed (`Burger_Applicatie.link`) and the bulk container
        retrieval (`Burger_Applicatie.Inzamelplek`).
        """
        for block in self._blocks(r'"datasource":\{'):
            if (
                self._value(block, "type") == "microflow"
                and self._value(block, "path") == entity
            ):
                if operation := self._value(block, "operationId"):
                    return operation
        return None

    def entity_path_datasource(self, entity: str) -> str | None:
        """The operation behind a list of `entity` reached over an association.

        Finds the tile and template lists, whose paths end in the entity, as in
        `Burger_Applicatie.Group_ResultSet_Groups/Burger_Applicatie.Group`.
        """
        for block in self._blocks(r'"datasource":\{'):
            path = self._value(block, "path") or ""
            if (
                self._value(block, "type") == "entityPath"
                and path.split("/")[-1] == entity
            ):
                if operation := self._value(block, "operationId"):
                    return operation
        return None

    # -- actions

    def microflow_call(self, parameter: str) -> str | None:
        """A nanoflow's call to a microflow taking one named parameter.

        Finds choosing a tile (`Group`) and choosing a template (`Template`).
        """
        for match in re.finditer(r'"type":"microflowCall"', self._text):
            # The identifier follows the type directly; the parameter name
            # comes later, so this window deliberately spans both.
            block = self._text[match.start() : match.start() + _WINDOW]
            if f'"name":"{parameter}"' in block:
                if operation := self._value(block, "operationId"):
                    return operation
        return None

    def call_with_arguments(self, arguments: set[str]) -> str | None:
        """A button's action whose argument map is exactly `arguments`.

        Finds the address submit, which takes a `link` and a `helper`. The
        identifier is read from the `config` that directly follows the argument
        map, so it cannot be confused with a nearby widget's own operation.
        """
        pattern = r'"argMap":\{(.{0,400}?)\},"config":\{(.{0,300}?)\}'
        for match in re.finditer(pattern, self._text, re.S):
            keys = set(re.findall(r'"(\w+)":\{"widget"', match.group(1)))
            if keys == arguments:
                return self._value(match.group(2), "operationId")
        return None

    # -- internals

    def _blocks(self, opening: str):
        for match in re.finditer(opening, self._text):
            yield self._segment(match.start())

    def _segment(self, start: int) -> str:
        """One widget's definition, cut before the next one starts."""
        end = start + _WINDOW
        for marker in _BOUNDARIES:
            following = self._text.find(marker, start + 1)
            if following != -1:
                end = min(end, following)
        return self._text[start:end]

    @staticmethod
    def _value(block: str, key: str) -> str | None:
        match = re.search(rf'"{key}":"([^"]*)"', block)
        return match.group(1) if match else None
