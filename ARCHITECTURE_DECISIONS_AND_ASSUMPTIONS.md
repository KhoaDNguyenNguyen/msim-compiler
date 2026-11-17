# Architecture Decisions, Engineering Assumptions, and Technical Rationale

## 1. System Context and Core Objectives
The `msim_compiler` synthesizer generates native NI Multisim 14 (`.ms14`) binary schematic files without launching the Multisim GUI. Prior to this refactoring, the system suffered from two critical simulation bugs and five major technical debts:
1. **Bug 1.1 (SPICE Archetype Mismatch)**: `place_part_from_db()` cloned raw XML strings from `self._archetypes` and fell back to `self._archetypes["RESISTOR"]` (`r%p %t1 %t2 #1`), turning Capacitors or Inductors into nano-ohm resistors in SPICE if string replacement missed.
2. **Bug 1.2 (Virtual Connector Net-Merging Short)**: In `instrument_testbench.ms14`, the Virtual Connector attached to oscilloscope `XSC4` (wired to node `VOUT` / `490440424`) retained the label `VIN` in its symbol text and component metadata, causing Multisim's Net-Merging engine to short-circuit `VIN` and `VOUT`.
3. **Technical Debts**: Temporary JSON file littering (`part_UUID.json`), fragile regular expressions mutating XML strings, hardcoded `CiNode` IDs without dynamic graph verification, unvalidated XOR password extraction on `MSCOMP_S.PRD`, and hardcoded fallback coordinates in `OpticalCircuitParser`.

---

## 2. Explicit Engineering Assumptions and Defensive Boundaries

### Assumption A1: Target Multisim Database Format (Jet 3/4 `MSCOMP_S.PRD`)
* **Assumption**: The primary execution environment targets NI Circuit Design Suite / Multisim 13.4 through 14.3, which stores master components in a Microsoft Jet OLEDB 4.0 database (`MSCOMP_S.PRD`) encrypted with a 20-byte static XOR header key at offset `0x42`.
* **Defensive Boundary**: Rather than assuming the database file is infallible, `MultisimDatabaseBridge._crack_jet3_password()` validates file existence, header length (`>= 128` bytes), and strict printable ASCII bounds (`32 <= byte <= 126`) on every decrypted byte. If the header is corrupted or uses an unsupported encryption format, `DatabaseDecryptionError` is raised immediately.

### Assumption A2: 32-Bit OLEDB Provider Requirement on 64-Bit Windows
* **Assumption**: `Microsoft.Jet.OLEDB.4.0` is a 32-bit native COM/OLEDB provider on Windows.
* **Rationale & Decision**: Compiling `msim_db_bridge.cs` with `AnyCPU` on 64-bit .NET causes runtime provider registration failures. Therefore, `MultisimDatabaseBridge.ensure_compiled()` explicitly compiles `msim_db_bridge.cs` with `/platform:x86` and wraps OLEDB operations in structured `try...catch` blocks returning deterministic exit codes (`2` for part not found, `3` for database/OLEDB exception).

### Assumption A3: Multisim 14 `CArchive` XML Attribute Slot Ordering
* **Assumption**: Multisim 14's C++ XML deserializer maps `<CiComponent><Attributes><Item>` elements positionally into fixed internal arrays (`CiaCollString`, `CiaParamList`, `CiaSpiceTmpltExprt`, `CiaProperDlg`, `CiaDOUBLE`).
* **Rationale & Decision**: Constructing arbitrary `<Attributes>` trees with missing `<Item/>` placeholders triggers Access Violations in Multisim. `ComponentDomBuilder` solves this by maintaining a sanitized, token-parameterized `xml.etree.ElementTree` attribute skeleton in `msim_compiler/config/ci_schema_skeletons.xml` (alongside a pure-Python `ET.Element` fallback generator) and populating all component values, SPICE templates, and metadata exclusively through `xml.etree.ElementTree` node traversal.

---

## 3. Architectural Decisions (ADR)

### ADR-01: Zero-Tempfile Inter-Process Communication via `stdout` Pipe
* **Decision**: `msim_db_bridge.exe` streams UTF-8 JSON directly to `Console.Out` when invoked with 3 arguments (`dbPath`, `password`, `queryName`). Python's `fetch_part()` captures `stdout` directly in RAM via `subprocess.run(..., capture_output=True)`.
* **Rationale**: Eliminates disk I/O overhead, prevents `part_UUID.json` file accumulation on process termination, and removes filesystem race conditions during parallel batch compilation.

### ADR-02: Pure `ElementTree` Component & Virtual Connector Synthesis (`ComponentDomBuilder`)
* **Decision**: Remove `self._archetypes` and `_extract_native_component_archetypes()` from `MultisimSchematicBuilder`. Introduce `ComponentDomBuilder` in `msim_compiler/component_builder.py` to build `<CiComponent>`, `<CiPort>`, `<CiModel>`, `<CIITSymbolComp>`, and `<CVirtualConnector>` as `ET.Element` trees.
* **Rationale**: Guarantees that every component family (`RESISTOR`, `CAPACITOR`, `INDUCTOR`, `DIODE`, `BJT`, `OPAMP`, `GROUND`, `VIRTUAL_CONN`) receives its exact SPICE prefix (`r%p`, `c%p`, `l%p`, `d%p`, `q%p`, `x%p`) and `<CiaParamList>` / `<CiaProperDlg>` numeric values through DOM API calls rather than fragile string replacements.

### ADR-03: Graph-Based Virtual Connector Synchronization on Testbench
* **Decision**: Implement `_synchronize_testbench_virtual_connectors_dom()` using `ElementTree` traversal. For every `<CVirtualConnector>`, the engine traces its `<CIITPinConnectorComp>` wire and `<CiPort>` to the connected `<CiNode>` and instrument island (`XFG1`/`XSC3` -> `VIN`, `XSC4` -> `VOUT`), synchronizing:
  1. `<CIITSymTextCompName Output="..."/>`
  2. `<Element NetModifier="&ASCNI_EWB_NET_VIRTUAL"><Item Value="..."/></Element>`
  3. `<CiComponent LocalName="..."/>` and `<CiaCollString>` items
  4. `<CSCH_CAPVirtualComponentPin lastnet="..."/>`
* **Rationale**: Eradicates Bug 1.2 (`VIN` / `VOUT` short-circuit on `XSC4`) deterministically without relying on blind regex replacements.

### ADR-04: Morphological Opening & Wire-Skeleton Tracing in `OpticalCircuitParser`
* **Decision**: Replace single-pass thresholding and the hardcoded 8-component fallback in `vision_parser.py` with a two-stage morphological pipeline:
  1. **Morphological Erosion (`r_erode = 4`)**: Strips thin interconnecting wires (`thickness <= 5px`) while preserving thick component bodies (`>= 16px`), allowing Disjoint-Set Connected Component Labeling (CCL) to isolate individual components accurately.
  2. **Wire-Skeleton Graph Extraction**: Masks detected component interiors and clusters remaining foreground wire pixels via DSU to bind component terminals (`Pin 1`, `Pin 2`) to their true electrical nets.
  3. **Strict Validation**: If an image contains zero valid component blobs, `ValueError` is raised immediately instead of injecting fake coordinates.

### ADR-05: Automated Pre-Flight Verification Gate (`validate_dom_integrity`)
* **Decision**: Before encoding any `.ms14` binary, `MultisimSchematicBuilder.validate_dom_integrity()` executes three mandatory DOM audits:
  1. **SPICE Prefix Audit**: Verifies every `C*` uses `c%p`, `L*` uses `l%p`, `R*` uses `r%p`, `D*` uses `d%p`, and `Q*` uses `q%p`.
  2. **Virtual Connector Consistency Audit**: Verifies all 4 DOM attributes of every `<CVirtualConnector>` match its connected `<CiNode>`.
  3. **Net Isolation Audit**: Verifies that `VIN`, `VOUT`, and `0` share zero common `<CiPort>` IDs.

### ADR-06: Topology-Aware & Parameterized Multi-Tone Harmonic Injection (Bug 1.3 Fix)
* **Decision**: Eliminate the hardcoded `if lname in ("&ASCR1", "&ASCR10"):` target hijack in `MultisimDOMManipulator.configure_xfg1_harmonics_dom()`. Expose `injection_target: Optional[str] = None` across `FuncGenConfig`, `MS14Document.configure_function_generator()`, and `MS14Document.auto_tune_all_instruments()`. When `injection_target` is not explicitly specified, the DOM engine inspects `<CiNode LocalName="&ASCVIN"><Ports>` and `<CiPort>` bindings to identify the exact circuit component and pin (`%t{pin_name}`) physically wired to `VIN`.
* **Rationale**: Prevents load resistors named `R1` or `R10` located at `VOUT` (such as in LC Pi or T filters) from being hijacked by `v_harm_*` sources, which previously caused an AC short-circuit (`0 dB` flat response on the Bode Plotter) between `VGEN` and `VOUT`, and supports arbitrary user-defined component naming and pin rotations.
### ADR-07: Native Global Ground (`NI_EWB_NET_GLOBAL_GROUND`) & Unique Virtual Connector NetModifier GUIDs
* **Decision**: Allocate a fresh UUID (`mod_guid`) for every `<CVirtualConnector>` `<Element NetModifier="&ASCNI_EWB_NET_VIRTUAL"><Item Guid="..."/></Element>` and register a corresponding `Modifier="8"` `<CSchDesignNetLabel>` in `<CDesignNetContainer>`. For synthesized `GROUND` symbols, inject `<Element NetModifier="&ASCNI_EWB_NET_GLOBAL_GROUND"><Item Value="&ASC0" Guid="..."/></Element>` and a `Modifier="1"` `<CSchDesignNetLabel LastOverruleName="&ASC0" LastNetName="&ASC0"/>` instead of parasitic `r_gnd_*` resistors.
* **Rationale**: Prevents duplicate `NetModifier` GUIDs (such as legacy `{333F0FA6-F265-4C2B-9350-5DC5E5240A98}` with `LastOverruleName="&ASCVOUT"`) from short-circuiting `VIN` and `VOUT`, and guarantees that Multisim's interactive SPICE netlister binds all shunt ground returns directly to global node `0` rather than an isolated floating net.

### ADR-08: Dedicated `VFG` Pre-Input Node for Multi-Tone Harmonic Synthesis
* **Decision**: When multi-tone harmonics are configured on `XFG1`, isolate `XFG1` positive terminal (`Port 495909056`) and its adjacent virtual connector (`ID 192713976`) onto a dedicated `CiNode` (`&ASCVFG`), and bridge `VFG` to `%t{pin}` (`VIN`) via 4-argument SPICE sinusoidal sources (`v_xfg_s*`) inside the primary input component template.
* **Rationale**: Ensures that the `VIN` node itself carries the full composite multi-tone waveform so both `XSC3` (Input FFT), `XSC1`/`XSC2` (Time Domain), and all parallel input branches observe all harmonic tones while preserving an unperturbed AC sweep path for `XBP1` (Bode Plotter).
