"""Pipeline dehydration helpers for hydrated Tangle pipeline specs.

The dehydrator is the inverse companion to :mod:`tangle_cli.pipeline_hydrator`:
it replaces full ``componentRef.spec`` blocks with portable digest/name/url/file
references, and can export a hydrated pipeline into a Jinja2 template + config
pair.  The code is intentionally native-free; downstream packages can provide a
client for component-library existence checks and URI reader/writer hooks for
schemes such as ``gs://`` without this module importing those SDKs.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import textwrap
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from . import utils
from .api_transport import DEFAULT_API_URL
from .handler import TangleCliHandler
from .logger import Logger, get_default_logger
from .pipeline_hydrator import PipelineHydrator, ResolverContext, UriReader, UriWriter

PATH_SEPARATOR = "|"  # Use | as separator since task names can contain dots.

# Per-component filename limit shared by ext4, APFS and NTFS. It counts
# BYTES, not characters, so a multi-byte stem has to be measured encoded.
_MAX_FILENAME_BYTES = 255


def _truncate_utf8(text: str, budget: int) -> str:
    """Return the longest prefix of *text* that fits *budget* UTF-8 bytes.

    Truncation lands on a character boundary: cutting the encoded form can
    leave a partial multi-byte sequence, and dropping it keeps the result
    decodable and deterministic.
    """
    if budget <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= budget:
        return text
    return encoded[:budget].decode("utf-8", "ignore")


class ResolveManifestUnavailableError(RuntimeError):
    """Raised when a portable resolve ref is required but has no path context.

    ``DIGEST``/``NAME`` always materialize a local fallback beside the output,
    so an in-memory dehydration with no output path cannot produce a portable
    reference. Failing is better than writing a surprising file into the cwd.
    """


class ComponentFilenameError(RuntimeError):
    """Base class for problems naming an extracted component file."""


class ComponentFilenameCollisionError(ComponentFilenameError):
    """Raised when two different component specs claim one extraction filename.

    Unreachable while filenames carry a full content address; it exists so that
    any future change which shortens or weakens the address fails loudly here
    instead of silently overwriting a previously extracted component.
    """


class ComponentFilenameTooLongError(ComponentFilenameError):
    """Raised when the configured extension leaves no room for a filename.

    The separator, content address and extension are reserved before the
    readable stem, so only an extension longer than the limit itself can make
    a valid name impossible. Reported here rather than as an ``OSError`` from
    whichever writer happened to receive the oversized path.
    """


@dataclass(frozen=True)
class Jinja2ExportResult:
    """Result of exporting a pipeline to Jinja2 templates."""

    main_template_path: Path
    config_file_path: Path
    subtemplates_count: int
    top_level_params_count: int
    subtemplate_paths: list[Path]


class DehydrateChoice:
    """Constants for dehydration choices.

    Lowercase values apply to the current component.  Downstream interactive
    callers may use uppercase values to remember a choice for the same digest.
    """

    DIGEST = "d"
    NAME = "n"
    URL = "u"
    FILE = "f"
    KEEP = "k"
    AUTO = "a"


class PipelineDehydrator(TangleCliHandler):
    """Dehydrate pipeline YAML by replacing full component specs with refs.

    Supported choices:
    - ``DIGEST``: replace with ``componentRef.digest``
    - ``NAME``: replace with ``componentRef.name``
    - ``URL``: replace with ``componentRef.url`` when a canonical URL exists
    - ``FILE``: extract the component spec and reference it by URL
    - ``KEEP``: preserve the full spec
    - ``AUTO``: URL if canonical, else digest when the optional client can find
      the component in the library, else file extraction

    URI I/O is delegated through the same native-free hooks as the hydrator.
    OSS registers no cloud schemes by default; downstream packages can pass or
    register URI hooks for ``gs://`` or other backends.
    """

    def __init__(
        self,
        remembered_choices: Mapping[str, str] | None = None,
        components_dir: Path | str | None = None,
        output_file: Path | str | None = None,
        client: Any = None,
        interactive: bool = False,
        logger: Logger | None = None,
        component_extension: str | None = None,
        *,
        base_url: str | None = None,
        client_factory: Callable[[], Any] | None = None,
        extract_subgraphs: bool = True,
        uri_readers: Mapping[str, UriReader] | None = None,
        uri_writers: Mapping[str, UriWriter] | None = None,
    ) -> None:
        super().__init__(
            client=client,
            client_factory=client_factory,
            logger=logger,
            base_url=base_url or DEFAULT_API_URL,
        )
        self.remembered_choices = dict(remembered_choices or {})
        self.extract_subgraphs = extract_subgraphs
        self.output_file = output_file
        self.component_extension = component_extension or ".yaml"

        self._components_dir_explicit = components_dir is not None
        if components_dir is not None:
            self.components_dir: Path | str = components_dir
        elif output_file is not None:
            self.components_dir = self._join_destination(self._destination_parent(output_file), "components")
        else:
            self.components_dir = Path("components")

        self.interactive = interactive
        self._saved_components: dict[str, Path | str] = {}
        self._component_filenames: dict[str, str] = {}
        # fragment id -> ordered resolve-config entries, written as one sidecar
        self._resolve_manifest: dict[str, list[dict[str, Any]]] = {}
        # content address -> written subgraph file; kept apart from components
        self._saved_subgraphs: dict[str, Path | str] = {}
        self._current_reference_file: Path | str | None = output_file
        self._io = PipelineHydrator(
            enable_resolution=False,
            logger=self.log,
            base_url=self.base_url,
            uri_readers=uri_readers,
            uri_writers=uri_writers,
        )

    def _is_auto_mode(self) -> bool:
        """Return True when any remembered choice asks for auto mode."""

        return DehydrateChoice.AUTO in self.remembered_choices.values()

    @staticmethod
    def _uri_scheme(value: Path | str | None) -> str | None:
        if value is None:
            return None
        return PipelineHydrator._uri_scheme(str(value))

    @classmethod
    def _is_local_destination(cls, value: Path | str | None) -> bool:
        scheme = cls._uri_scheme(value)
        return scheme is None or scheme == "file"

    @classmethod
    def _destination_parent(cls, value: Path | str) -> Path | str:
        value_str = str(value)
        scheme = cls._uri_scheme(value)
        if scheme and scheme != "file":
            return value_str.rsplit("/", 1)[0] if "/" in value_str else value_str
        path = Path(value_str[7:] if value_str.startswith("file://") else value_str)
        return path.parent

    @classmethod
    def _join_destination(cls, parent: Path | str, filename: str) -> Path | str:
        if cls._uri_scheme(parent) and cls._uri_scheme(parent) != "file":
            return f"{str(parent).rstrip('/')}/{filename}"
        return Path(parent) / filename

    def _resolver_context(self, uri: str, kind: str) -> ResolverContext:
        return self._io.make_resolver_context(self._uri_scheme(uri) or kind, uri, kind, None)

    def _read_text(self, source: Path | str, *, kind: str = "pipeline") -> str:
        return self._io._read_uri_text(str(source), kind, self._resolver_context(str(source), kind)) or ""

    def _write_text(self, destination: Path | str, content: str, *, kind: str = "output") -> None:
        self._io._write_uri_text(str(destination), content, self._resolver_context(str(destination), kind))

    def load_file(self, input_file: Path | str) -> dict[str, Any]:
        """Read a local or URI pipeline YAML file through the registered hooks."""

        data = yaml.safe_load(self._read_text(input_file, kind="pipeline"))
        return data or {}

    def write_file(self, data: dict[str, Any], output_file: Path | str | None = None) -> None:
        """Write pipeline YAML to a local path or URI through registered hooks."""

        destination = output_file or self.output_file
        if destination is None:
            raise ValueError("output_file is required")
        self._write_text(destination, utils.dump_yaml(data), kind="output")

    def dehydrate_file(
        self,
        input_file: Path | str,
        output_file: Path | str | None = None,
    ) -> dict[str, Any]:
        """Read, dehydrate, and write a pipeline YAML file.

        Both input and output support local paths and any URI schemes provided
        by registered/passed hydrator URI hooks.
        """

        previous_output = self.output_file
        previous_reference = self._current_reference_file
        previous_components_dir = self.components_dir
        if output_file is not None:
            self.output_file = output_file
            self._current_reference_file = output_file
            if not self._components_dir_explicit:
                self.components_dir = self._join_destination(self._destination_parent(output_file), "components")
        try:
            data = self.load_file(input_file)
            output = self.dehydrate(data)
            self.write_file(output, output_file)
            return output
        finally:
            self.output_file = previous_output
            self._current_reference_file = previous_reference
            self.components_dir = previous_components_dir

    def _auto_dehydrate_choice(
        self,
        canonical_url: str | None,
        resolved_digest: str,
        name: str,
        spec: dict[str, Any],
        path: str,
    ) -> tuple[str, str | None]:
        """Auto outcome ``url``/``digest``/``file``, plus the digest to emit.

        A digest is emitted only when its own published spec is this component.
        """

        self.log.info(f"   Auto: '{name}' at {path} (digest: {resolved_digest[:16]}...)")
        if canonical_url:
            self.log.info("   Auto: has canonical URL -> url ref")
            return "url", None
        current = self._verified_digest(resolved_digest, spec, path)
        if current is None:
            return "file", None
        self.log.info(f"   Auto: digest {current[:16]} found in library -> digest ref")
        return "digest", current

    def _content_digest(self, spec: Any) -> str:
        """Normalized-content digest, computed the way the hydrator sees specs.

        Never a locator: a published digest often addresses the component's
        source TEXT while an inline spec hashes its sorted content, so equal
        components legitimately carry different digests.
        """
        return utils.compute_spec_digest(self._io.normalize_component_spec(spec))

    def _verified_digest(self, digest: str, spec: Mapping[str, Any], path: str) -> str | None:
        """``digest`` itself when its OWN published spec is this component.

        The digest is verified, not its current successor: following a later
        deprecation is accepted product behavior, even when the successor's
        content differs, so hydration is left to do that as it always has.
        """
        if not digest or digest == "unknown":
            return None
        # Client creation can exit on missing credentials; both it and the
        # lookup degrade to local-only rather than failing the dehydration.
        try:
            client = self._get_client()
        except (Exception, SystemExit):
            client = None
        if client is None:
            self.log.info(f"   No component library client for {path} -> local only")
            return None
        try:
            published = client.get_component_spec(digest)
        except Exception:
            self.log.info(f"   Digest {digest[:16]} not in library -> local only")
            return None
        if self._content_digest(published) != self._content_digest(spec):
            self.log.info(f"   Digest {digest[:16]} resolves elsewhere -> local only")
            return None
        return digest

    def _verified_digest_primary(
        self, digest: str, spec: Mapping[str, Any], path: str
    ) -> dict[str, Any] | None:
        verified = self._verified_digest(digest, spec, path)
        return {"digest": verified} if verified else None

    def _verified_name_primary(
        self, digest: str, spec: Mapping[str, Any], path: str
    ) -> dict[str, Any] | None:
        """``{name, publisher}`` from the inspected publication of ``digest``.

        This pins the AUTHOR, not the version: hydration resolves that owner's
        latest candidate. The inspected spec must equal the inline one, and the
        emitted name is the published one rather than any inline placeholder.
        """
        from .authenticated_identity import is_symbolic_me
        from .component_inspector import ComponentInspector

        if not digest or digest == "unknown":
            return None
        try:
            client = self._get_client()
        except (Exception, SystemExit):
            client = None
        if client is None:
            self.log.info(f"   No component library client for {path} -> local only")
            return None
        try:
            inspected = ComponentInspector(client=client, logger=self.log).inspect_by_digest(
                digest, full_spec=True
            )
        except Exception:
            self.log.info(f"   Inspection failed for {path} -> local only")
            return None
        published = inspected.get("spec")
        if (
            inspected.get("status") != "success"
            or published is None
            or self._content_digest(published) != self._content_digest(spec)
        ):
            self.log.info(f"   No matching publication for {path} -> local only")
            return None
        name, owner = inspected.get("name"), inspected.get("published_by")
        if not isinstance(name, str) or not name.strip():
            self.log.info(f"   Publication for {path} has no name -> local only")
            return None
        if not isinstance(owner, str) or not owner.strip() or is_symbolic_me(owner):
            self.log.info(f"   Untrustworthy owner for {path} -> local only")
            return None
        return {"name": name, "publisher": owner}

    def _manifest_path(self) -> Path:
        """``<output stem>.components.yaml`` beside a LOCAL output file."""
        output = self.output_file
        if output is None or not self._is_local_destination(output):
            raise ResolveManifestUnavailableError(
                "digest/name dehydration writes a resolve config and a local "
                "fallback beside the output, so it needs a local output_file; "
                "none was given."
            )
        if not self._is_local_destination(self.components_dir):
            raise ResolveManifestUnavailableError(
                "digest/name dehydration needs a local components_dir for its "
                "fallback copies."
            )
        text = str(output)
        path = Path(text[7:] if text.startswith("file://") else text)
        return (path.parent / f"{path.stem}.components.yaml").resolve()

    def _portable_resolve_ref(
        self, name: str, spec: dict[str, Any], primary: dict[str, Any] | None
    ) -> dict[str, str]:
        """Materialize ``spec`` locally and reference it through the manifest.

        The fragment is an ordered list, so the local copy is used only when
        the primary fails; a single mapping would instead invoke the
        resolver's version comparison and could prefer the local copy.
        """
        manifest = self._manifest_path()
        self._save_component_to_file(name, spec)
        saved = Path(str(self._saved_components[utils.compute_spec_digest(spec)]))
        # The fragment addresses the whole entry list: the content (via the
        # content-addressed file) AND the primary. Equal specs reached through
        # different verified locators are distinct fragments, not a conflict.
        fragment = saved.stem
        if primary:
            policy = json.dumps(primary, sort_keys=True).encode("utf-8")
            fragment = f"{fragment}-{hashlib.sha256(policy).hexdigest()[:12]}"
        local = os.path.relpath(saved, manifest.parent).replace(os.sep, "/")
        # The marker lets the hydrator fall through to the local copy when the
        # primary raises (404, offline); the local entry stays unmarked.
        marked = [{**primary, "fallback_on_error": True}] if primary else []
        self._resolve_manifest[fragment] = [*marked, {"local": f"./{local}"}]
        ref_text = str(self._current_reference_file or self.output_file)
        ref_dir = Path(ref_text[7:] if ref_text.startswith("file://") else ref_text).parent.resolve()
        rel = os.path.relpath(manifest, ref_dir).replace(os.sep, "/")
        return {"url": f"resolve://./{rel}#{fragment}"}

    def _write_resolve_manifest(self) -> None:
        if self._resolve_manifest:
            manifest = self._manifest_path()
            ordered = {k: self._resolve_manifest[k] for k in sorted(self._resolve_manifest)}
            self._write_text(manifest, utils.dump_yaml(ordered), kind="resolve config")

    def _portable_primary(
        self, choice: str, name: str, digest: str, spec: Mapping[str, Any], path: str
    ) -> dict[str, Any] | None:
        if choice == DehydrateChoice.NAME:
            return self._verified_name_primary(digest, spec, path)
        return self._verified_digest_primary(digest, spec, path)

    def _extract_subgraphs_portable(self, data: dict[str, Any], choice: str) -> None:
        """Replace every inline graph boundary with a manifest-backed ref.

        Deepest first, so a boundary is written only after its own nested
        boundaries are already refs. Each extracted graph lives in the
        components bundle, and its filename addresses the dehydrated content
        that is actually written.
        """
        bundle_file = self._join_destination(self.components_dir, "_")
        root_reference = self._current_reference_file
        for depth, path in _build_subgraph_processing_queue(data):
            if depth == 0:
                continue
            result = _get_subgraph_by_path(data, path)
            if not result:
                continue
            component_ref, spec = result
            spec_name = str(spec.get("name", "subgraph"))
            try:
                self._current_reference_file = bundle_file
                written = utils.traverse_pipeline_tasks(
                    copy.deepcopy(spec), spec_name, self._process_task
                )
                digest = component_ref.get("digest") or utils.compute_spec_digest(spec)
                primary = self._portable_primary(choice, spec_name, digest, spec, path)
                # The boundary ref lives in its parent: the root for depth 1,
                # otherwise another extracted graph in the bundle.
                self._current_reference_file = root_reference if depth == 1 else bundle_file
                new_ref = self._portable_resolve_ref(spec_name, written, primary)
            finally:
                self._current_reference_file = root_reference
            component_ref.clear()
            component_ref.update(new_ref)

    def _prompt_choice(self, name: str, digest: str, canonical_url: str | None, path: str) -> str:
        self.log.info(f"\n📦 Found componentRef at: {path}")
        self.log.info(f"   Name: {name}")
        self.log.info(f"   Digest: {digest[:16]}...")
        if canonical_url:
            self.log.info(f"   URL: {canonical_url}")
        self.log.info("   Options:")
        self.log.info(f"     [{DehydrateChoice.DIGEST}] Replace with componentRef.digest")
        self.log.info(f"     [{DehydrateChoice.NAME}] Replace with componentRef.name")
        if canonical_url:
            self.log.info(f"     [{DehydrateChoice.URL}] Replace with componentRef.url")
        self.log.info(f"     [{DehydrateChoice.FILE}] Extract to file and use file:// URL")
        self.log.info(f"     [{DehydrateChoice.AUTO}] Auto: URL if present, else digest if in library, else file")
        self.log.info(f"     [{DehydrateChoice.KEEP}] Leave as is (keep full spec)")
        self.log.info(f"     [{DehydrateChoice.DIGEST.upper()}] Always replace this component with digest")
        self.log.info(f"     [{DehydrateChoice.NAME.upper()}] Always replace this component with name")
        if canonical_url:
            self.log.info(f"     [{DehydrateChoice.URL.upper()}] Always replace this component with URL")
        self.log.info(f"     [{DehydrateChoice.FILE.upper()}] Always extract to file")
        choice = input(f"   Choice [{DehydrateChoice.AUTO}]: ").strip() or DehydrateChoice.AUTO
        return choice

    def _process_task(
        self,
        task_name: str,
        task_data: dict[str, Any],
        path: str,
        base_dir: Path | None = None,
        _recursive_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Dehydrate a single non-subgraph task's componentRef."""

        del task_name, base_dir, _recursive_params
        if not isinstance(task_data, dict) or "componentRef" not in task_data:
            return task_data

        component_ref = task_data["componentRef"]
        if not isinstance(component_ref, dict) or "spec" not in component_ref:
            return task_data

        name, digest = utils.get_component_ref_info(component_ref)
        spec = component_ref.get("spec", {})
        if not isinstance(spec, dict):
            return task_data

        canonical_url = spec.get("metadata", {}).get("annotations", {}).get("canonical_location")
        resolved_digest = component_ref.get("digest") or utils.compute_spec_digest(spec)
        choice = (
            self.remembered_choices.get(resolved_digest)
            or self.remembered_choices.get(digest)
            or self.remembered_choices.get("")
        )
        if choice:
            if choice == DehydrateChoice.URL and not canonical_url:
                choice = DehydrateChoice.DIGEST
            if choice != DehydrateChoice.AUTO:
                self.log.info(f"   Using remembered choice: {choice}")
        elif self.interactive:
            choice = self._prompt_choice(name, digest, canonical_url, path)
            if choice == DehydrateChoice.DIGEST.upper():
                self.remembered_choices[resolved_digest] = DehydrateChoice.DIGEST
                choice = DehydrateChoice.DIGEST
            elif choice == DehydrateChoice.NAME.upper():
                self.remembered_choices[resolved_digest] = DehydrateChoice.NAME
                choice = DehydrateChoice.NAME
            elif choice == DehydrateChoice.URL.upper() and canonical_url:
                self.remembered_choices[resolved_digest] = DehydrateChoice.URL
                choice = DehydrateChoice.URL
            elif choice == DehydrateChoice.FILE.upper():
                self.remembered_choices[resolved_digest] = DehydrateChoice.FILE
                choice = DehydrateChoice.FILE
        else:
            choice = DehydrateChoice.AUTO

        new_task = {k: v for k, v in task_data.items() if k != "componentRef"}

        if choice == DehydrateChoice.AUTO:
            effective, current = self._auto_dehydrate_choice(
                canonical_url, resolved_digest, name, spec, path
            )
            if effective == "url":
                new_task["componentRef"] = {"url": canonical_url}
                self.log.info("   → Auto: Replaced with componentRef.url")
            elif effective == "digest":
                new_task["componentRef"] = {"digest": current}
                self.log.info("   → Auto: Replaced with componentRef.digest (found in library)")
            else:
                file_url = self._save_component_to_file(name, spec)
                new_task["componentRef"] = {"url": file_url}
                self.log.info("   → Auto: Extracted to file (no URL, not in library or no client)")
        elif choice == DehydrateChoice.DIGEST:
            primary = self._verified_digest_primary(resolved_digest, spec, path)
            new_task["componentRef"] = self._portable_resolve_ref(name, spec, primary)
            self.log.info(
                "   → Replaced with componentRef.url (resolve config: "
                f"{'digest + local' if primary else 'local only'})"
            )
        elif choice == DehydrateChoice.NAME:
            primary = self._verified_name_primary(resolved_digest, spec, path)
            new_task["componentRef"] = self._portable_resolve_ref(name, spec, primary)
            self.log.info(
                "   → Replaced with componentRef.url (resolve config: "
                f"{'name + local' if primary else 'local only'})"
            )
        elif choice == DehydrateChoice.URL and canonical_url:
            new_task["componentRef"] = {"url": canonical_url}
            self.log.info("   → Replaced with componentRef.url")
        elif choice == DehydrateChoice.FILE:
            file_url = self._save_component_to_file(name, spec)
            new_task["componentRef"] = {"url": file_url}
            self.log.info(f"   → Extracted to {file_url}")
        else:
            new_task["componentRef"] = component_ref
            self.log.info("   → Kept as componentRef (full spec)")

        return new_task

    def _safe_filename(self, name: str, fallback: str = "component") -> str:
        safe_name = name.lower().replace(" ", "_").replace("-", "_")
        safe_name = "".join(c for c in safe_name if c.isalnum() or c == "_")
        return safe_name or fallback

    def _component_filename(self, name: str, address: str) -> str:
        """Return ``<stem>-<address><extension>``, byte-bounded.

        The readable stem takes whatever UTF-8 bytes the address and extension
        leave; it is dropped, separator included, when nothing fits.
        """
        stemless = f"{address}{self.component_extension}"
        required = len(stemless.encode("utf-8"))
        if required > _MAX_FILENAME_BYTES:
            raise ComponentFilenameTooLongError(
                f"component_extension is too long to build a component filename: the "
                f"content address and extension need {required} bytes, over the "
                f"{_MAX_FILENAME_BYTES}-byte limit"
            )
        # The separator is charged here rather than reserved above, so an
        # extension that fits by exactly one byte is not rejected.
        stem = _truncate_utf8(self._safe_filename(name), _MAX_FILENAME_BYTES - required - 1)
        if not stem:
            return stemless
        # ``_safe_filename`` folds ``-`` to ``_``, so this separator cannot
        # occur inside the stem.
        return f"{stem}-{stemless}"

    def _save_component_to_file(self, name: str, spec: dict[str, Any]) -> str:
        """Write a component spec once and return a reference URL for it."""

        # Address the spec, never ``componentRef.digest``: that digest is a
        # locator, not proof of content. The hydrator digests raw source TEXT,
        # so equal specs can carry different valid digests, and an edited spec
        # can still carry the stale digest of what it used to be.
        address = utils.compute_spec_digest(spec)
        if address not in self._saved_components:
            filename = self._component_filename(name, address)
            claimed_by = self._component_filenames.get(filename)
            if claimed_by is not None and claimed_by != address:
                raise ComponentFilenameCollisionError(
                    f"extracted component filename {filename!r} is already taken by a "
                    f"different component spec; refusing to overwrite it"
                )
            self._component_filenames[filename] = address
            destination = self._join_destination(self.components_dir, filename)
            self._write_text(destination, utils.dump_yaml(spec), kind="component")
            if self._is_local_destination(destination):
                destination_text = str(destination)
                if destination_text.startswith("file://"):
                    destination_text = destination_text[7:]
                destination = Path(destination_text).resolve()
            self._saved_components[address] = destination
        return self._make_ref_url(self._saved_components[address])

    def _make_ref_url(self, target: Path | str) -> str:
        """Create a componentRef URL for a saved target."""

        if not self._is_local_destination(target):
            return str(target)
        return self._make_file_url(Path(str(target)[7:] if str(target).startswith("file://") else str(target)))

    def _make_file_url(self, target_path: Path) -> str:
        """Create a file:// URL relative to the current reference file."""

        ref_file = self._current_reference_file or self.output_file
        if ref_file and self._is_local_destination(ref_file):
            ref_str = str(ref_file)
            ref_path = Path(ref_str[7:] if ref_str.startswith("file://") else ref_str)
            ref_dir = ref_path.parent.resolve()
            rel = os.path.relpath(target_path.resolve(), ref_dir)
            return f"file://./{rel}"
        return f"file://{target_path.resolve()}"

    @staticmethod
    def _relativize_file_urls(spec: dict[str, Any], reference_dir: Path) -> None:
        """Convert absolute file:// URLs in a spec's tasks relative to reference_dir."""

        tasks = spec.get("implementation", {}).get("graph", {}).get("tasks", {})
        resolved_ref_dir = reference_dir.resolve()
        for task_data in tasks.values():
            if not isinstance(task_data, dict):
                continue
            component_ref = task_data.get("componentRef")
            if not isinstance(component_ref, dict) or "url" not in component_ref:
                continue
            url = component_ref["url"]
            if not isinstance(url, str) or not url.startswith("file:///"):
                continue
            abs_path = Path(url[7:])
            rel = os.path.relpath(abs_path, resolved_ref_dir)
            component_ref["url"] = f"file://./{rel}"

    def _subgraph_destination(self, filename: str) -> Path | str:
        if self.output_file is not None:
            subgraph_dir = self._join_destination(self._destination_parent(self.output_file), "subgraphs")
        else:
            subgraph_dir = self._join_destination(self.components_dir, "subgraphs")
        return self._join_destination(subgraph_dir, filename)

    def _extract_subgraphs_to_files(self, data: dict[str, Any]) -> dict[str, Any]:
        """Extract inline subgraph specs to content-addressed YAML files.

        A filename addresses the fully written subgraph, so identical subgraphs
        share one file and two outputs in one directory can only ever collide on
        identical bytes. A per-run counter let a later output overwrite an
        earlier output's subgraph with different content. Deepest first, so a
        parent's address covers its children's content-addressed names.
        """

        # Relative URLs inside a subgraph depend only on its directory, so they
        # are computed against a placeholder there before the address is known.
        placeholder = self._subgraph_destination("_")
        for depth, path in _build_subgraph_processing_queue(data):
            if depth == 0:
                continue

            result = _get_subgraph_by_path(data, path)
            if not result:
                continue
            component_ref, spec = result
            spec_name = str(spec.get("name", "subgraph"))

            original_ref = self._current_reference_file
            self._current_reference_file = placeholder
            try:
                spec_to_write = utils.traverse_pipeline_tasks(copy.deepcopy(spec), spec_name, self._process_task)
            finally:
                self._current_reference_file = original_ref
            local = self._is_local_destination(placeholder)
            if local:
                placeholder_text = str(placeholder)
                placeholder_path = Path(placeholder_text[7:] if placeholder_text.startswith("file://") else placeholder_text)
                self._relativize_file_urls(spec_to_write, placeholder_path.parent)

            address = utils.compute_spec_digest(spec_to_write)
            destination = self._saved_subgraphs.get(address)
            if destination is None:
                filename = self._component_filename(spec_name, address)
                destination = self._subgraph_destination(filename)
                if local:
                    text = str(destination)
                    destination = Path(text[7:] if text.startswith("file://") else text).resolve()
                # Always (re)written once per run: a file already on disk under
                # this name is not trusted to still hold this content.
                self._write_text(destination, utils.dump_yaml(spec_to_write) + "\n", kind="subgraph")
                self._saved_subgraphs[address] = destination
                self.log.info(f"   📦 Extracted subgraph '{spec_name}' -> {filename}")
            component_ref.clear()
            component_ref["url"] = f"file://{destination}" if local else str(destination)

        if self.output_file and self._is_local_destination(self.output_file):
            output_file_text = str(self.output_file)
            if output_file_text.startswith("file://"):
                output_file_text = output_file_text[7:]
            output_path = Path(output_file_text)
            self._relativize_file_urls(data, output_path.parent)

        return data

    def dehydrate(self, data: dict[str, Any]) -> dict[str, Any]:
        """Return a dehydrated copy of *data* according to configured choices."""

        working = copy.deepcopy(data)
        # One output per call. ``dehydrate_file``/``export_to_jinja2`` retarget
        # output_file and components_dir between calls, so extraction caches
        # from an earlier output would point this one at the old bundle.
        self._saved_components = {}
        self._component_filenames = {}
        self._resolve_manifest = {}
        self._saved_subgraphs = {}
        self._current_reference_file = self.output_file
        default_choice = self.remembered_choices.get("")
        # Every choice that promises a dehydrated document must replace nested
        # graph boundaries too; otherwise they keep their inline spec. A nested
        # graph has no canonical URL of its own, so URL extracts it to a file.
        #
        # ``extract_subgraphs=False`` keeps every boundary inline and dehydrates
        # only the leaf components inside it, in place. It is for callers that
        # represent nested graphs themselves (the decompiler turns each into
        # Python), so the output is deliberately not a fully dehydrated document.
        if not self.extract_subgraphs:
            pass
        elif default_choice in (DehydrateChoice.AUTO, DehydrateChoice.FILE, DehydrateChoice.URL):
            self._extract_subgraphs_to_files(working)
        elif default_choice in (DehydrateChoice.DIGEST, DehydrateChoice.NAME):
            self._extract_subgraphs_portable(working, default_choice)

        pipeline_name = working.get("name", "pipeline")
        result = utils.traverse_pipeline_tasks(working, str(pipeline_name), self._process_task)
        self._write_resolve_manifest()
        return result

    def export_to_jinja2(
        self,
        data: dict[str, Any],
        output_file: Path,
        jinja2_path: Path,
    ) -> Jinja2ExportResult:
        """Dehydrate a pipeline and export it to Jinja2 template files."""

        previous_output = self.output_file
        previous_reference = self._current_reference_file
        previous_components_dir = self.components_dir
        self.output_file = output_file
        self._current_reference_file = output_file
        if not self._components_dir_explicit:
            self.components_dir = self._join_destination(self._destination_parent(output_file), "components")
        try:
            output_yaml = self.dehydrate(data)
        finally:
            self.output_file = previous_output
            self._current_reference_file = previous_reference
            self.components_dir = previous_components_dir

        jinja2_path.parent.mkdir(parents=True, exist_ok=True)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        base_name = jinja2_path.stem
        if base_name.endswith(".yaml"):
            base_name = base_name[:-5]

        top_level_defaults = _extract_input_defaults(output_yaml)
        modified_data, subtemplates = _process_subgraphs_to_subtemplates(output_yaml, self.log)
        template_data = _replace_input_defaults_with_placeholders(modified_data)

        subtemplate_paths: list[Path] = []
        for subtemplate_id, subtemplate_info in subtemplates.items():
            subtemplate_file = jinja2_path.parent / f"{base_name}_{subtemplate_id}.yaml.j2"
            subtemplate_yaml = utils.dump_yaml(subtemplate_info["spec"])

            path_depth = subtemplate_info["path"].count(PATH_SEPARATOR) // 2
            indent = " " * (12 * path_depth)
            subtemplate_yaml = textwrap.indent(subtemplate_yaml, indent)
            subtemplate_yaml = _convert_templateid_to_includes(subtemplate_yaml, subtemplates, base_name)

            subtemplate_file.write_text(subtemplate_yaml, encoding="utf-8")
            subtemplate_paths.append(subtemplate_file)
            self.log.info(f"   📄 Wrote {subtemplate_file.name}")

        main_yaml = utils.dump_yaml(template_data)
        main_yaml = _convert_templateid_to_includes(main_yaml, subtemplates, base_name)
        jinja2_path.write_text(main_yaml, encoding="utf-8")

        try:
            rel_template_path = jinja2_path.relative_to(output_file.parent)
        except ValueError:
            rel_template_path = jinja2_path

        config_data: dict[str, Any] = {"template_file": str(rel_template_path), **top_level_defaults}
        output_file.write_text(utils.dump_yaml(config_data), encoding="utf-8")

        return Jinja2ExportResult(
            main_template_path=jinja2_path,
            config_file_path=output_file,
            subtemplates_count=len(subtemplates),
            top_level_params_count=len(top_level_defaults),
            subtemplate_paths=subtemplate_paths,
        )


def _extract_input_defaults(data: dict[str, Any]) -> dict[str, Any]:
    """Extract default values from top-level inputs."""

    defaults: dict[str, Any] = {}
    inputs = data.get("inputs", [])
    if isinstance(inputs, list):
        for input_spec in inputs:
            if isinstance(input_spec, dict) and "name" in input_spec and "default" in input_spec:
                defaults[_sanitize_variable_name(str(input_spec["name"]))] = input_spec["default"]
    elif isinstance(inputs, dict):
        for name, input_def in inputs.items():
            if isinstance(input_def, dict) and "default" in input_def:
                defaults[_sanitize_variable_name(str(name))] = input_def["default"]
    return defaults


def _replace_input_defaults_with_placeholders(data: dict[str, Any]) -> dict[str, Any]:
    """Replace top-level input defaults with Jinja2 placeholders."""

    modified = copy.deepcopy(data)
    inputs = modified.get("inputs", [])
    if isinstance(inputs, list):
        for input_spec in inputs:
            if isinstance(input_spec, dict) and "name" in input_spec and "default" in input_spec:
                var_name = _sanitize_variable_name(str(input_spec["name"]))
                input_spec["default"] = "{{ " + var_name + " }}"
    elif isinstance(inputs, dict):
        for name, input_def in inputs.items():
            if isinstance(input_def, dict) and "default" in input_def:
                var_name = _sanitize_variable_name(str(name))
                input_def["default"] = "{{ " + var_name + " }}"
    return modified


def _sanitize_variable_name(name: str) -> str:
    """Convert a name to a valid Jinja2 variable name."""

    sanitized = re.sub(r"[^\w]", "_", name.lower())
    sanitized = re.sub(r"_+", "_", sanitized)
    return sanitized.strip("_")


def _convert_templateid_to_includes(
    yaml_text: str,
    subtemplates: Mapping[str, Mapping[str, Any]],
    base_name: str,
) -> str:
    """Convert templateId markers in YAML to Jinja2 include syntax."""

    def replace_with_include(match: re.Match[str], template_file: str) -> str:
        name_value = match.group(1).strip()
        if not (name_value.startswith("'") or name_value.startswith('"')):
            name_value = f"'{name_value}'"
        return f"{{% with _subgraph_name = {name_value} %}}{{% include '{template_file}' %}}{{% endwith %}}"

    for subtemplate_id in subtemplates:
        template_filename = f"{base_name}_{subtemplate_id}.yaml.j2"
        yaml_text = re.sub(
            rf"^\s*templateId:\s*{re.escape(subtemplate_id)}\s*\n\s*_subgraph_name:\s*(.+?)\s*$",
            lambda m: replace_with_include(m, template_filename),
            yaml_text,
            flags=re.MULTILINE,
        )
    return yaml_text


def _build_subgraph_processing_queue(data: dict[str, Any]) -> list[tuple[int, str]]:
    """Build subgraph paths ordered deepest-first."""

    results: list[tuple[int, str]] = []
    stack: list[tuple[dict[str, Any], str, int]] = [(data, "", 0)]

    while stack:
        spec, current_path, depth = stack.pop()
        spec_name = spec.get("name", "unnamed")
        path = f"{current_path}{PATH_SEPARATOR}{spec_name}" if current_path else str(spec_name)
        results.append((depth, path))

        tasks = spec.get("implementation", {}).get("graph", {}).get("tasks", {})
        for task_name, task_data in tasks.items():
            if not isinstance(task_data, dict):
                continue
            component_ref = task_data.get("componentRef")
            if not isinstance(component_ref, dict):
                continue
            nested_spec = component_ref.get("spec", {})
            if utils.is_subgraph_spec(nested_spec):
                stack.append((nested_spec, f"{path}{PATH_SEPARATOR}{task_name}", depth + 1))

    return sorted(results, key=lambda item: (-item[0], item[1]))


def _get_task_component_ref(spec: dict[str, Any], task_name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(componentRef, nested_spec)`` for a task in a spec graph."""

    tasks = spec.get("implementation", {}).get("graph", {}).get("tasks", {})
    task_data = tasks.get(task_name, {})
    component_ref = task_data.get("componentRef", {})
    nested_spec = component_ref.get("spec", {}) if isinstance(component_ref, dict) else {}
    return component_ref, nested_spec


def _get_subgraph_by_path(data: dict[str, Any], path: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Resolve a subgraph's componentRef and spec by queue path."""

    path_parts = path.split(PATH_SEPARATOR)
    if len(path_parts) < 3:
        return None
    current_spec = data
    for i in range(1, len(path_parts) - 2, 2):
        task_name = path_parts[i]
        _, current_spec = _get_task_component_ref(current_spec, task_name)

    parent_task_name = path_parts[-2]
    component_ref, spec = _get_task_component_ref(current_spec, parent_task_name)
    if not spec:
        return None
    return component_ref, spec


def _spec_hash(spec: dict[str, Any]) -> str:
    """Compute a hash key for a spec dictionary, ignoring top-level name."""

    spec_for_hash = {k: v for k, v in spec.items() if k != "name"}
    return json.dumps(spec_for_hash, sort_keys=True)


def _process_subgraphs_to_subtemplates(
    data: dict[str, Any],
    logger: Logger | None = None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Extract subgraph specs into reusable subtemplate records."""

    log = logger or get_default_logger()
    working = copy.deepcopy(data)
    queue = _build_subgraph_processing_queue(working)
    subtemplates_by_hash: dict[str, dict[str, Any]] = {}
    subtemplate_counter = 0

    for depth, path in queue:
        if depth == 0:
            continue

        result = _get_subgraph_by_path(working, path)
        if not result:
            continue
        component_ref, spec = result

        spec_key = _spec_hash(spec)
        spec_name = spec.get("name", "unnamed")
        if spec_key in subtemplates_by_hash:
            subtemplate_id = subtemplates_by_hash[spec_key]["id"]
            log.info(f"   ♻️  Reusing {subtemplate_id} for '{spec_name}'")
        else:
            subtemplate_id = f"subtemplate_{subtemplate_counter}"
            subtemplate_counter += 1
            spec_copy = copy.deepcopy(spec)
            if "name" in spec_copy:
                spec_copy["name"] = "{{ _subgraph_name }}"
            subtemplates_by_hash[spec_key] = {"id": subtemplate_id, "spec": spec_copy, "path": path}
            log.info(f"   📦 Created {subtemplate_id} for '{spec_name}'")

        component_ref["spec"] = {"templateId": subtemplate_id, "_subgraph_name": spec_name}

    subtemplates = {
        info["id"]: {"spec": info["spec"], "path": info["path"]}
        for info in subtemplates_by_hash.values()
    }
    return working, subtemplates


__all__ = [
    "DehydrateChoice",
    "Jinja2ExportResult",
    "PipelineDehydrator",
    "PATH_SEPARATOR",
    "_build_subgraph_processing_queue",
    "_convert_templateid_to_includes",
    "_extract_input_defaults",
    "_get_subgraph_by_path",
    "_process_subgraphs_to_subtemplates",
    "_replace_input_defaults_with_placeholders",
    "_sanitize_variable_name",
]
