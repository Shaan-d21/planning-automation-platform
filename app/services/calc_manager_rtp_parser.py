"""Defensive parser for Calc Manager XML and LCM ZIP exports."""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from xml.etree import ElementTree

from app.models.business_rule_rtp import RuntimePromptDefinition
from app.utils.exceptions import BusinessRuleError


@dataclass(frozen=True, slots=True)
class ParsedRuleRTPDefinition:
    """Rule definition discovered in one source document."""

    rule_name: str
    cube_name: str | None
    source_path: str
    prompts: tuple[RuntimePromptDefinition, ...]


@dataclass(frozen=True, slots=True)
class CalcManagerParseResult:
    """Validated definitions plus non-fatal parser diagnostics."""

    definitions: tuple[ParsedRuleRTPDefinition, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _HBRVariable:
    """One variable definition from Oracle's HBRRepo export format."""

    variable_id: str
    name: str
    value_type: str
    usage: str
    product: str
    properties: dict[str, str]
    default_value: str | None
    limit_type: str | None
    limit_value: str | None


class CalcManagerRTPParser:
    """Parse supported RTP nodes without trusting archive contents."""

    PARSER_VERSION = "2.1"
    MAX_PACKAGE_BYTES = 50 * 1024 * 1024
    MAX_ARCHIVE_ENTRIES = 5_000
    MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
    MAX_XML_BYTES = 20 * 1024 * 1024

    _PROMPT_TAGS = {
        "rtp",
        "runtimeprompt",
        "runtimepromptdefinition",
        "runtimepromptvariable",
    }
    _RULE_TAGS = {"rule", "businessrule"}
    _NAME_KEYS = ("name", "rtpname", "variablename")
    _RULE_NAME_KEYS = ("rulename", "businessrulename", "name")
    _TYPE_KEYS = ("type", "valuetype", "datatype")
    _DEFAULT_KEYS = ("default", "defaultvalue", "value")
    _DIMENSION_KEYS = ("dimension", "dimensionname")

    def parse(self, source_name: str, content: bytes) -> CalcManagerParseResult:
        """Parse an XML document or ZIP package into rule RTP contracts."""
        safe_name = PurePosixPath(str(source_name).replace("\\", "/")).name
        if not safe_name:
            raise BusinessRuleError("The Calc Manager export requires a file name.")
        if not content:
            raise BusinessRuleError("The Calc Manager export is empty.")
        if len(content) > self.MAX_PACKAGE_BYTES:
            raise BusinessRuleError("The Calc Manager export exceeds the 50 MB limit.")

        documents: list[tuple[str, bytes]] = []
        if zipfile.is_zipfile(io.BytesIO(content)):
            documents.extend(self._zip_documents(content))
        elif safe_name.casefold().endswith(".xml"):
            documents.append((safe_name, content))
        else:
            raise BusinessRuleError(
                "Provide a Calc Manager XML file or LCM ZIP package."
            )

        definitions: dict[str, ParsedRuleRTPDefinition] = {}
        warnings: list[str] = []
        for path, xml_bytes in documents:
            parsed, document_warnings = self._parse_xml(path, xml_bytes)
            warnings.extend(document_warnings)
            for definition in parsed:
                key = definition.rule_name.casefold()
                previous = definitions.get(key)
                if previous is None:
                    definitions[key] = definition
                    continue
                merged = self._merge_prompts(previous, definition)
                definitions[key] = merged
                warnings.append(
                    f"Rule '{definition.rule_name}' occurred in multiple XML files; "
                    "its RTP definitions were merged."
                )

        if not definitions:
            raise BusinessRuleError(
                "No supported Business Rule runtime-prompt definitions were found. "
                "The existing registry was not changed."
            )
        return CalcManagerParseResult(
            definitions=tuple(sorted(definitions.values(), key=lambda item: item.rule_name.casefold())),
            warnings=tuple(dict.fromkeys(warnings)),
        )

    def _zip_documents(self, content: bytes) -> list[tuple[str, bytes]]:
        documents: list[tuple[str, bytes]] = []
        total_size = 0
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > self.MAX_ARCHIVE_ENTRIES:
                raise BusinessRuleError("The LCM archive contains too many entries.")
            for entry in entries:
                total_size += max(0, entry.file_size)
                if total_size > self.MAX_UNCOMPRESSED_BYTES:
                    raise BusinessRuleError("The expanded LCM archive exceeds the safety limit.")
                if entry.flag_bits & 0x1:
                    raise BusinessRuleError("Encrypted LCM archives are not supported.")
                if entry.is_dir():
                    continue
                if entry.file_size > self.MAX_XML_BYTES:
                    if self._is_xml_archive_candidate(entry.filename):
                        raise BusinessRuleError(
                            f"LCM XML entry '{entry.filename}' exceeds the 20 MB limit."
                        )
                    continue
                if not self._is_xml_archive_candidate(entry.filename):
                    continue
                document = archive.read(entry)
                if self._looks_like_xml(document):
                    documents.append((entry.filename, document))
        if not documents:
            raise BusinessRuleError("The LCM archive does not contain XML documents.")
        return documents

    @staticmethod
    def _is_xml_archive_candidate(entry_name: str) -> bool:
        """Recognize XML manifests and Oracle's extensionless resources."""
        normalized = str(entry_name).replace("\\", "/").casefold()
        return normalized.endswith(".xml") or "/resource/" in f"/{normalized}"

    @staticmethod
    def _looks_like_xml(content: bytes) -> bool:
        """Avoid parsing non-XML binaries stored below an LCM resource path."""
        prefix = content[:512].lstrip(b"\xef\xbb\xbf\x00\x09\x0a\x0d\x20")
        return prefix.startswith(b"<")

    def _parse_xml(
        self,
        source_path: str,
        content: bytes,
    ) -> tuple[list[ParsedRuleRTPDefinition], list[str]]:
        if len(content) > self.MAX_XML_BYTES:
            raise BusinessRuleError(f"XML document '{source_path}' exceeds the 20 MB limit.")
        prefix = content[:4096].upper()
        if b"<!DOCTYPE" in prefix or b"<!ENTITY" in prefix:
            raise BusinessRuleError(
                f"XML document '{source_path}' contains a prohibited DTD or entity declaration."
            )
        try:
            root = ElementTree.fromstring(content)
        except ElementTree.ParseError as exc:
            raise BusinessRuleError(f"Calc Manager XML '{source_path}' is invalid: {exc}.") from exc

        if self._local_name(root.tag) == "hbrrepo":
            return self._parse_hbr_repo(source_path, root)

        discovered: dict[
            str,
            tuple[str, str | None, list[RuntimePromptDefinition]],
        ] = {}

        def visit(element: ElementTree.Element, rule_name: str | None, cube: str | None) -> None:
            local = self._local_name(element.tag)
            values = self._element_values(element)
            if local in self._RULE_TAGS:
                rule_name = self._first(values, self._RULE_NAME_KEYS) or rule_name
                cube = self._first(values, ("plantype", "cube", "cubename")) or cube
            if local in self._PROMPT_TAGS:
                prompt_rule = self._first(values, ("rulename", "businessrulename")) or rule_name
                if prompt_rule:
                    prompt = self._prompt(
                        values,
                        len(discovered.get(prompt_rule.casefold(), (prompt_rule, None, []))[2]) + 1,
                    )
                    if prompt is not None:
                        stored_name, stored_cube, prompts = discovered.setdefault(
                            prompt_rule.casefold(),
                            (prompt_rule, cube, []),
                        )
                        if stored_cube is None and cube is not None:
                            discovered[prompt_rule.casefold()] = (
                                stored_name,
                                cube,
                                prompts,
                            )
                        prompts.append(prompt)
            for child in element:
                visit(child, rule_name, cube)

        visit(root, None, None)

        # Some exports store one rule per XML and omit a rule wrapper. The
        # file name is only used when RTP nodes exist and no explicit rule
        # relationship is available.
        if not discovered:
            orphan_prompts: list[RuntimePromptDefinition] = []
            for element in root.iter():
                if self._local_name(element.tag) not in self._PROMPT_TAGS:
                    continue
                prompt = self._prompt(self._element_values(element), len(orphan_prompts) + 1)
                if prompt is not None:
                    orphan_prompts.append(prompt)
            if orphan_prompts:
                inferred = PurePosixPath(source_path).stem.strip()
                if inferred:
                    discovered[inferred.casefold()] = (inferred, None, orphan_prompts)

        definitions: list[ParsedRuleRTPDefinition] = []
        warnings: list[str] = []
        for key, (stored_rule_name, cube_name, prompts) in discovered.items():
            unique: dict[str, RuntimePromptDefinition] = {}
            for prompt in prompts:
                unique.setdefault(prompt.name.casefold(), prompt)
            rule_name = self._discover_rule_name(root, key) or stored_rule_name
            definitions.append(
                ParsedRuleRTPDefinition(
                    rule_name=rule_name,
                    cube_name=cube_name,
                    source_path=source_path,
                    prompts=tuple(
                        RuntimePromptDefinition(
                            name=item.name,
                            label=item.label,
                            prompt_order=index,
                            value_type=item.value_type,
                            dimension=item.dimension,
                            default_value=item.default_value,
                            has_default=item.has_default,
                            required_at_launch=item.required_at_launch,
                            hidden=item.hidden,
                            allow_multiple=item.allow_multiple,
                            security_mode=item.security_mode,
                            scope_type=item.scope_type,
                            scope_name=item.scope_name,
                            source_variable_id=item.source_variable_id,
                            limit_type=item.limit_type,
                            limit_value=item.limit_value,
                            source_metadata=dict(item.source_metadata),
                        )
                        for index, item in enumerate(
                            sorted(
                                unique.values(),
                                key=lambda prompt: prompt.prompt_order,
                            ),
                            1,
                        )
                    ),
                )
            )
            if len(unique) != len(prompts):
                warnings.append(f"Duplicate RTP names were ignored for rule '{rule_name}'.")
        return definitions, warnings

    def _parse_hbr_repo(
        self,
        source_path: str,
        root: ElementTree.Element,
    ) -> tuple[list[ParsedRuleRTPDefinition], list[str]]:
        """Resolve scoped variables referenced by rules in an HBRRepo export."""
        export_version = str(root.attrib.get("version") or "unknown").strip()
        variables = [
            self._hbr_variable(element)
            for container in root
            if self._local_name(container.tag) == "variables"
            for element in container
            if self._local_name(element.tag) == "variable"
        ]
        by_id = {
            variable.variable_id: variable
            for variable in variables
            if variable.variable_id
        }
        by_name: dict[str, list[_HBRVariable]] = {}
        for variable in variables:
            by_name.setdefault(variable.name.casefold(), []).append(variable)

        definitions: list[ParsedRuleRTPDefinition] = []
        warnings: list[str] = []
        rules = [
            element
            for container in root
            if self._local_name(container.tag) == "rules"
            for element in container
            if self._local_name(element.tag) == "rule"
        ]
        for rule in rules:
            rule_name = str(rule.attrib.get("name") or "").strip()
            if not rule_name:
                warnings.append(f"An HBRRepo rule in '{source_path}' has no name and was skipped.")
                continue
            rule_id = str(rule.attrib.get("id") or "").strip()
            rule_properties = self._hbr_properties(rule)
            application = rule_properties.get("application")
            cube = rule_properties.get("plantype") or rule_properties.get("cube")
            references = [
                reference
                for container in rule
                if self._local_name(container.tag) == "variablereferences"
                for reference in container
                if self._local_name(reference.tag) == "variablereference"
            ]
            prompts: list[RuntimePromptDefinition] = []
            for fallback_order, reference in enumerate(references, 1):
                reference_name = str(reference.attrib.get("name") or "").strip()
                reference_id = str(reference.attrib.get("id") or "").strip()
                variable = by_id.get(reference_id)
                if variable is None:
                    variable = self._resolve_hbr_variable(
                        by_name.get(reference_name.casefold(), []),
                        application=application,
                        cube=cube,
                        rule_id=rule_id,
                        rule_name=rule_name,
                    )
                if variable is None:
                    warnings.append(
                        f"Rule '{rule_name}' references variable '{reference_name or reference_id}' "
                        "whose definition was not included in the export."
                    )
                    continue
                prompts.append(
                    self._hbr_prompt(
                        variable,
                        reference,
                        fallback_order=fallback_order,
                        export_version=export_version,
                        rule_id=rule_id,
                        rule_name=rule_name,
                    )
                )
            if not prompts:
                continue
            unique: dict[str, RuntimePromptDefinition] = {}
            for prompt in prompts:
                unique.setdefault(prompt.name.casefold(), prompt)
            ordered = tuple(
                RuntimePromptDefinition(
                    name=prompt.name,
                    label=prompt.label,
                    prompt_order=index,
                    value_type=prompt.value_type,
                    dimension=prompt.dimension,
                    default_value=prompt.default_value,
                    has_default=prompt.has_default,
                    required_at_launch=prompt.required_at_launch,
                    hidden=prompt.hidden,
                    allow_multiple=prompt.allow_multiple,
                    security_mode=prompt.security_mode,
                    scope_type=prompt.scope_type,
                    scope_name=prompt.scope_name,
                    source_variable_id=prompt.source_variable_id,
                    limit_type=prompt.limit_type,
                    limit_value=prompt.limit_value,
                    source_metadata=dict(prompt.source_metadata),
                )
                for index, prompt in enumerate(
                    sorted(unique.values(), key=lambda item: item.prompt_order),
                    1,
                )
            )
            definitions.append(
                ParsedRuleRTPDefinition(
                    rule_name=rule_name,
                    cube_name=cube,
                    source_path=source_path,
                    prompts=ordered,
                )
            )
            if len(unique) != len(prompts):
                warnings.append(f"Duplicate RTP names were ignored for rule '{rule_name}'.")

        if variables and not rules:
            warnings.append(
                "The HBRRepo export contains variable definitions but no Business Rules. "
                "Export the rules together with their referenced variables to build rule contracts."
            )
        return definitions, warnings

    def _hbr_variable(self, element: ElementTree.Element) -> _HBRVariable:
        properties = self._hbr_properties(element)
        raw_default = self._hbr_child_text(element, "value", preserve_empty=True)
        default_value = raw_default.strip() if raw_default and raw_default.strip() else None
        limits = next(
            (child for child in element if self._local_name(child.tag) == "limits"),
            None,
        )
        limit_properties = self._hbr_properties(limits) if limits is not None else {}
        return _HBRVariable(
            variable_id=str(element.attrib.get("id") or "").strip(),
            name=str(element.attrib.get("name") or "").strip(),
            value_type=str(element.attrib.get("type") or "text").strip(),
            usage=str(element.attrib.get("usage") or "").strip(),
            product=str(element.attrib.get("product") or "Planning").strip(),
            properties=properties,
            default_value=default_value,
            limit_type=(str(limits.attrib.get("type") or "").strip() or None) if limits is not None else None,
            limit_value=limit_properties.get("value"),
        )

    def _hbr_prompt(
        self,
        variable: _HBRVariable,
        reference: ElementTree.Element,
        *,
        fallback_order: int,
        export_version: str,
        rule_id: str,
        rule_name: str,
    ) -> RuntimePromptDefinition:
        reference_properties = self._hbr_properties(reference)
        properties = variable.properties
        prompt_text = properties.get("display_label") or properties.get("prompt_text")
        label = (
            prompt_text
            if prompt_text and not self._is_resource_key(prompt_text)
            else variable.name
        )
        normalized_type = self._hbr_value_type(variable.value_type)
        dimension = properties.get("dimension") or self._hbr_dimension(
            properties.get("dimensionType"),
            variable.name,
            normalized_type,
        )
        has_default = variable.default_value is not None
        allow_missing = self._bool(properties.get("allowMissing"), default=False)
        hidden = self._bool(reference_properties.get("hidden"), default=False)
        scope_type, scope_name = self._hbr_scope(variable)
        metadata = {
            "adapter": "HBRRepo",
            "export_version": export_version,
            "product": variable.product,
            "usage": variable.usage,
            "rule_id": rule_id,
            "rule_name": rule_name,
        }
        for source_key, target_key in (
            ("dimensionType", "dimension_type"),
            ("dimensionInputMode", "dimension_input_mode"),
            ("useLastValue", "use_last_value"),
            ("allowMissing", "allow_missing"),
            ("validation_value", "validation_value"),
            ("type", "reference_type"),
        ):
            value = reference_properties.get(source_key) or properties.get(source_key)
            if value is not None and value != "":
                metadata[target_key] = value
        return RuntimePromptDefinition(
            name=variable.name,
            label=label,
            prompt_order=self._integer(reference_properties.get("seq"), fallback_order),
            value_type=normalized_type,
            dimension=dimension,
            default_value=variable.default_value,
            has_default=has_default,
            required_at_launch=not has_default and not allow_missing,
            hidden=hidden,
            allow_multiple=normalized_type == "MEMBERS",
            security_mode=(
                reference_properties.get("securityMode")
                or reference_properties.get("security")
                or properties.get("securityMode")
                or properties.get("security")
            ),
            scope_type=scope_type,
            scope_name=scope_name,
            source_variable_id=variable.variable_id or None,
            limit_type=variable.limit_type,
            limit_value=variable.limit_value,
            source_metadata=metadata,
        )

    @classmethod
    def _resolve_hbr_variable(
        cls,
        candidates: list[_HBRVariable],
        *,
        application: str | None,
        cube: str | None,
        rule_id: str,
        rule_name: str,
    ) -> _HBRVariable | None:
        def score(variable: _HBRVariable) -> int:
            properties = variable.properties
            variable_application = properties.get("application")
            variable_cube = properties.get("plantype")
            variable_rule_id = properties.get("rule")
            variable_rule_name = properties.get("rule_name")
            if variable_application and application and variable_application.casefold() != application.casefold():
                return -10_000
            if variable_cube and cube and variable_cube.casefold() != cube.casefold():
                return -10_000
            result = 0
            if variable_rule_id and variable_rule_id == rule_id:
                result += 1_000
            elif variable_rule_name and variable_rule_name.casefold() == rule_name.casefold():
                result += 900
            elif variable_rule_id or variable_rule_name:
                return -10_000
            if variable_cube and cube:
                result += 100
            if variable_application and application:
                result += 10
            return result

        ranked = sorted(((score(item), item) for item in candidates), key=lambda item: item[0], reverse=True)
        return ranked[0][1] if ranked and ranked[0][0] >= 0 else None

    @classmethod
    def _hbr_scope(cls, variable: _HBRVariable) -> tuple[str, str | None]:
        properties = variable.properties
        if properties.get("rule") or properties.get("rule_name"):
            return "RULE", properties.get("rule_name") or properties.get("rule")
        if properties.get("plantype"):
            return "CUBE", properties["plantype"]
        if properties.get("application"):
            return "APPLICATION", properties["application"]
        return "GLOBAL", variable.product or None

    @staticmethod
    def _hbr_value_type(value: str) -> str:
        return {
            "member": "MEMBER",
            "members": "MEMBERS",
            "num": "NUMBER",
            "number": "NUMBER",
            "integer": "INTEGER",
            "percent": "PERCENT",
            "date_as_num": "DATE",
            "str_as_num": "TEXT",
            "string": "TEXT",
        }.get(str(value).strip().casefold(), str(value).strip().upper() or "TEXT")

    @staticmethod
    def _hbr_dimension(
        dimension_type: str | None,
        variable_name: str,
        value_type: str,
    ) -> str | None:
        if not dimension_type:
            return variable_name if value_type in {"MEMBER", "MEMBERS"} else None
        if dimension_type.strip().casefold() == "year":
            return "Years"
        return dimension_type.strip()

    @classmethod
    def _hbr_properties(cls, element: ElementTree.Element | None) -> dict[str, str]:
        if element is None:
            return {}
        return {
            str(child.attrib.get("name") or "").strip(): (child.text or "").strip()
            for child in element
            if cls._local_name(child.tag) == "property" and child.attrib.get("name")
        }

    @classmethod
    def _hbr_child_text(
        cls,
        element: ElementTree.Element,
        child_name: str,
        *,
        preserve_empty: bool = False,
    ) -> str | None:
        for child in element:
            if cls._local_name(child.tag) != cls._key(child_name):
                continue
            value = child.text or ""
            return value if preserve_empty else (value.strip() or None)
        return None

    @staticmethod
    def _is_resource_key(value: str) -> bool:
        normalized = value.strip().upper()
        return normalized.startswith(("ID_", "LABEL_")) and " " not in normalized

    def _prompt(self, values: dict[str, str], order: int) -> RuntimePromptDefinition | None:
        name = self._first(values, self._NAME_KEYS)
        if not name:
            return None
        default = self._first(values, self._DEFAULT_KEYS, preserve_empty=True)
        has_default = default is not None and default != ""
        required_value = self._first(values, ("required", "mandatory", "promptatlaunch"))
        required = self._bool(required_value, default=not has_default)
        return RuntimePromptDefinition(
            name=name,
            label=self._first(values, ("label", "displayname", "prompttext")) or name,
            prompt_order=self._integer(self._first(values, ("order", "sequence", "index")), order),
            value_type=(self._first(values, self._TYPE_KEYS) or "TEXT").upper(),
            dimension=self._first(values, self._DIMENSION_KEYS),
            default_value=default if has_default else None,
            has_default=has_default,
            required_at_launch=required,
            hidden=self._bool(self._first(values, ("hidden", "ishidden"))),
            allow_multiple=self._bool(self._first(values, ("multiple", "allowmultiple"))),
            security_mode=self._first(values, ("securitymode", "security")),
        )

    @classmethod
    def _element_values(cls, element: ElementTree.Element) -> dict[str, str]:
        values = {
            cls._key(name): str(value).strip()
            for name, value in element.attrib.items()
        }
        for child in element:
            if len(child) == 0 and child.text is not None:
                values.setdefault(cls._key(child.tag), child.text.strip())
        return values

    @classmethod
    def _first(
        cls,
        values: dict[str, str],
        keys: tuple[str, ...],
        *,
        preserve_empty: bool = False,
    ) -> str | None:
        for key in keys:
            normalized = cls._key(key)
            if normalized in values and (preserve_empty or values[normalized]):
                return values[normalized]
        return None

    @staticmethod
    def _local_name(value: str) -> str:
        return CalcManagerRTPParser._key(str(value).split("}")[-1])

    @staticmethod
    def _key(value: str) -> str:
        return re.sub(r"[^a-z0-9]", "", str(value).casefold().split("}")[-1])

    @staticmethod
    def _bool(value: str | None, *, default: bool = False) -> bool:
        if value is None:
            return default
        return value.strip().casefold() in {"1", "true", "yes", "y"}

    @staticmethod
    def _integer(value: str | None, default: int) -> int:
        try:
            return max(1, int(str(value).strip()))
        except (TypeError, ValueError):
            return default

    @classmethod
    def _discover_rule_name(cls, root: ElementTree.Element, normalized: str) -> str | None:
        for element in root.iter():
            if cls._local_name(element.tag) not in cls._RULE_TAGS:
                continue
            name = cls._first(cls._element_values(element), cls._RULE_NAME_KEYS)
            if name and name.casefold() == normalized:
                return name
        return None

    @staticmethod
    def _merge_prompts(
        left: ParsedRuleRTPDefinition,
        right: ParsedRuleRTPDefinition,
    ) -> ParsedRuleRTPDefinition:
        prompts = {item.name.casefold(): item for item in left.prompts}
        for item in right.prompts:
            prompts.setdefault(item.name.casefold(), item)
        ordered = tuple(
            RuntimePromptDefinition(
                name=item.name,
                label=item.label,
                prompt_order=index,
                value_type=item.value_type,
                dimension=item.dimension,
                default_value=item.default_value,
                has_default=item.has_default,
                required_at_launch=item.required_at_launch,
                hidden=item.hidden,
                allow_multiple=item.allow_multiple,
                security_mode=item.security_mode,
                scope_type=item.scope_type,
                scope_name=item.scope_name,
                source_variable_id=item.source_variable_id,
                limit_type=item.limit_type,
                limit_value=item.limit_value,
                source_metadata=dict(item.source_metadata),
            )
            for index, item in enumerate(prompts.values(), 1)
        )
        return ParsedRuleRTPDefinition(
            rule_name=left.rule_name,
            cube_name=left.cube_name or right.cube_name,
            source_path=f"{left.source_path}; {right.source_path}",
            prompts=ordered,
        )


def definition_checksum(definition: ParsedRuleRTPDefinition) -> str:
    """Return a stable checksum used for audit and change detection."""
    canonical = [definition.rule_name, definition.cube_name or ""]
    for prompt in definition.prompts:
        canonical.extend(
            [
                prompt.name,
                prompt.label,
                str(prompt.prompt_order),
                prompt.value_type,
                prompt.dimension or "",
                prompt.default_value or "",
                str(prompt.has_default),
                str(prompt.required_at_launch),
                str(prompt.hidden),
                str(prompt.allow_multiple),
                prompt.security_mode or "",
                prompt.scope_type,
                prompt.scope_name or "",
                prompt.source_variable_id or "",
                prompt.limit_type or "",
                prompt.limit_value or "",
                repr(sorted(prompt.source_metadata.items())),
            ]
        )
    return hashlib.sha256("\x1f".join(canonical).encode("utf-8")).hexdigest()
