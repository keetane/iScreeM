import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { ComponentType, DragEvent } from "react";
import type { LucideProps } from "lucide-react";
import {
  Activity,
  AlertTriangle,
  Beaker,
  Boxes,
  ChevronRight,
  CircleDotDashed,
  Database,
  Download,
  ExternalLink,
  Eye,
  EyeOff,
  FlaskConical,
  Focus,
  FolderPlus,
  Hexagon,
  Play,
  RefreshCw,
  Scale,
  Search,
  Server,
  Trash2,
  Upload,
  Workflow,
  X,
} from "lucide-react";
import type { ColumnDef } from "@tanstack/react-table";
import { api } from "./api";
import type {
  ChainSequence,
  DockingResult,
  DockingUpload,
  Grid,
  Job,
  Ligand,
  Pocket,
  Project,
  StructureLigand,
  Tool,
} from "./types";
import { Viewer } from "./components/Viewer";
import { GridEditor } from "./components/GridEditor";
import { DataTable } from "./components/DataTable";

const KetcherDialog = lazy(() =>
  import("./components/KetcherDialog").then((module) => ({
    default: module.KetcherDialog,
  })),
);

type Tab = "protein" | "ligands" | "setup" | "results" | "jobs" | "licenses";
const AlphaHelix = (props: LucideProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" {...props}>
    <path d="M7 3c6 0 10 3 10 6s-4 6-10 6" />
    <path d="M17 9c-6 0-10 3-10 6s4 6 10 6" />
    <path d="M8.5 5.5h7M7.5 10.5h9M7.5 15.5h9M8.5 20.5h7" />
  </svg>
);
const tabs: { id: Tab; label: string; icon: ComponentType<LucideProps> }[] = [
  { id: "protein", label: "Protein & Pocket", icon: AlphaHelix },
  { id: "ligands", label: "Ligand Preparation", icon: Hexagon },
  { id: "setup", label: "Docking Setup", icon: Workflow },
  { id: "results", label: "Docking Results", icon: Database },
  { id: "jobs", label: "Job Monitor", icon: Activity },
  { id: "licenses", label: "OSS / Licenses", icon: Scale },
];
const pocketColors = [
  "#47d7e8",
  "#65e6b4",
  "#f3b84b",
  "#c48cff",
  "#ff7f83",
  "#8db6ff",
];
const pocketColorForRank = (rank: number) =>
  pocketColors[Math.max(0, rank - 1) % pocketColors.length];
const structureLigandKey = (ligand: StructureLigand) =>
  `${ligand.chain || "*"}:${ligand.residue_number || "*"}:${ligand.name}`;
type ViewerFocusRequest =
  | { kind: "grid"; grid: Grid; nonce: number }
  | { kind: "ligand"; ligand: StructureLigand; nonce: number };
const ossLicenses = [
  {
    name: "smina",
    role: "Molecular docking engine",
    license: "Apache-2.0 / GPL-2.0 components; executable not distributed",
    source: "https://github.com/mwojcikowski/smina",
    licenseUrl: "https://github.com/mwojcikowski/smina/blob/master/LICENSE.GNU",
  },
  {
    name: "gnina",
    role: "CNN-scored molecular docking (Linux/WSL only)",
    license: "Apache-2.0 / GPL-2.0 components; executable not distributed",
    source: "https://github.com/gnina/gnina",
    licenseUrl: "https://github.com/gnina/gnina/blob/master/LICENSE.GNU",
  },
  {
    name: "P2Rank",
    role: "Protein pocket prediction",
    license: "MIT",
    source: "https://github.com/rdk/p2rank",
    licenseUrl: "https://github.com/rdk/p2rank/blob/develop/LICENSE.txt",
  },
  {
    name: "Redis",
    role: "RQ job queue data store",
    license: "7.2: BSD-3-Clause; 8+: AGPL-3.0 / RSALv2 / SSPLv1",
    source: "https://github.com/redis/redis",
    licenseUrl: "https://redis.io/legal/licenses/",
  },
  {
    name: "3Dmol.js",
    role: "Interactive molecular viewer",
    license: "BSD-3-Clause",
    source: "https://github.com/3dmol/3Dmol.js",
    licenseUrl: "https://github.com/3dmol/3Dmol.js/blob/master/LICENSE",
  },
  {
    name: "PDBFixer",
    role: "Missing atom and residue repair",
    license: "MIT",
    source: "https://github.com/openmm/pdbfixer",
    licenseUrl: "https://github.com/openmm/pdbfixer/blob/master/LICENSE",
  },
  {
    name: "OpenMM",
    role: "Molecular modelling used by PDBFixer",
    license: "MIT / LGPL components",
    source: "https://github.com/openmm/openmm",
    licenseUrl:
      "https://github.com/openmm/openmm/blob/master/docs-source/licenses/Licenses.txt",
  },
  {
    name: "PDB2PQR",
    role: "Protonation, charge and radius assignment",
    license: "BSD",
    source: "https://github.com/Electrostatics/pdb2pqr",
    licenseUrl:
      "https://github.com/Electrostatics/pdb2pqr/blob/main/LICENSE.md",
  },
  {
    name: "Meeko",
    role: "Ligand preparation",
    license: "LGPL-2.1-or-later",
    source: "https://github.com/forlilab/Meeko",
    licenseUrl: "https://github.com/forlilab/Meeko/blob/develop/LICENSE",
  },
  {
    name: "RDKit",
    role: "Cheminformatics and molecular descriptors",
    license: "BSD-3-Clause",
    source: "https://github.com/rdkit/rdkit",
    licenseUrl: "https://github.com/rdkit/rdkit/blob/master/license.txt",
  },
  {
    name: "Ketcher",
    role: "Embedded chemical structure editor and SMILES export",
    license: "Apache-2.0",
    source: "https://github.com/epam/ketcher",
    licenseUrl: "https://github.com/epam/ketcher/blob/master/LICENSE",
  },
  {
    name: "FastAPI",
    role: "Backend web API",
    license: "MIT",
    source: "https://github.com/fastapi/fastapi",
    licenseUrl: "https://github.com/fastapi/fastapi/blob/master/LICENSE",
  },
  {
    name: "React",
    role: "Web user interface",
    license: "MIT",
    source: "https://github.com/facebook/react",
    licenseUrl: "https://github.com/facebook/react/blob/main/LICENSE",
  },
];

export default function App() {
  const [tab, setTab] = useState<Tab>("protein"),
    [tools, setTools] = useState<Record<string, Tool>>({}),
    [projects, setProjects] = useState<{ id: string; name: string }[]>([]),
    [project, setProject] = useState<Project>(),
    [jobs, setJobs] = useState<Job[]>([]);
  const [activeGrid, setActiveGrid] = useState<Grid>(),
    [selectedProteinId, setSelectedProteinId] = useState<string>(),
    [visiblePockets, setVisiblePockets] = useState<Set<string>>(new Set()),
    [visibleStructureLigands, setVisibleStructureLigands] = useState<
      Set<string>
    >(new Set()),
    [selectedResidues, setSelectedResidues] = useState<Set<string>>(new Set()),
    [selectedLigands, setSelectedLigands] = useState<Set<string>>(new Set()),
    [dockingResults, setDockingResults] = useState<DockingResult[]>([]),
    [selectedResultKeys, setSelectedResultKeys] = useState<Set<string>>(
      new Set(),
    ),
    [visibleResultKeys, setVisibleResultKeys] = useState<Set<string>>(
      new Set(),
    ),
    [activePose, setActivePose] = useState<DockingResult>(),
    [dockingPoseFocus, setDockingPoseFocus] = useState<{
      key: string;
      nonce: number;
    }>(),
    [focusRequest, setFocusRequest] = useState<ViewerFocusRequest>(),
    [notice, setNotice] = useState<{ type: "error" | "info"; text: string }>(),
    [busy, setBusy] = useState(false);
  const protein =
    project?.proteins.find((item) => item.id === selectedProteinId) ||
    project?.proteins[0];
  const selectedDockingPoses = useMemo(() => {
    const displayed = new Set(visibleResultKeys);
    if (activePose) displayed.add(activePose.key);
    return dockingResults
      .filter((row) => displayed.has(row.key))
      .map((row) => ({ key: row.key, url: row.pose_url, pose: row.pose }));
  }, [dockingResults, visibleResultKeys, activePose]);
  useEffect(() => {
    setVisibleStructureLigands(
      new Set((protein?.stats.hetero_molecules || []).map(structureLigandKey)),
    );
  }, [protein?.id]);
  useEffect(() => {
    if (tab !== "results" || !project) return;
    api
      .dockingResults(project.id)
      .then((rows) => {
        setDockingResults(rows);
        const available = new Set(rows.map((row) => row.key));
        setSelectedResultKeys(
          (current) =>
            new Set([...current].filter((key) => available.has(key))),
        );
        setVisibleResultKeys(
          (current) =>
            new Set([...current].filter((key) => available.has(key))),
        );
        setActivePose((current) =>
          current && rows.some((row) => row.key === current.key)
            ? current
            : rows[0],
        );
      })
      .catch((error) =>
        setNotice({
          type: "error",
          text: error instanceof Error ? error.message : String(error),
        }),
      );
  }, [tab, project?.id]);
  const toggleSelectedResidue = useCallback(
    (key: string) =>
      setSelectedResidues((current) => {
        const next = new Set(current);
        next.has(key) ? next.delete(key) : next.add(key);
        return next;
      }),
    [],
  );
  const clearSelectedResidues = useCallback(
    () => setSelectedResidues(new Set()),
    [],
  );
  const refresh = useCallback(
    async (id?: string) => {
      const [t, p, j] = await Promise.all([
        api.tools(),
        api.projects(),
        api.jobs(),
      ]);
      setTools(t);
      setProjects(p);
      setJobs(j);
      const savedId = localStorage.getItem("mds.projectId");
      const projectId =
        id ||
        project?.id ||
        (savedId && p.some((x) => x.id === savedId) ? savedId : p[0]?.id);
      if (projectId) {
        const full = await api.project(projectId);
        const activeProteinId =
          selectedProteinId &&
          full.proteins.some((item) => item.id === selectedProteinId)
            ? selectedProteinId
            : full.proteins[0]?.id;
        const proteinPocketIds = new Set(
          full.pockets
            .filter((item) => item.protein_id === activeProteinId)
            .map((item) => item.id),
        );
        setProject(full);
        localStorage.setItem("mds.projectId", full.id);
        setSelectedProteinId(activeProteinId);
        setActiveGrid((current) =>
          current &&
          (!current.pocket_id || proteinPocketIds.has(current.pocket_id))
            ? full.grids.find((x) => x.id === current.id)
            : full.grids.find(
                (x) => !x.pocket_id || proteinPocketIds.has(x.pocket_id),
              ),
        );
        setVisiblePockets(
          new Set(
            full.pockets
              .filter((item) => item.protein_id === activeProteinId)
              .map((x) => x.id),
          ),
        );
      }
    },
    [project?.id, selectedProteinId],
  );
  useEffect(() => {
    refresh().catch((e) => setNotice({ type: "error", text: e.message }));
  }, []);
  const terminalJobs = useRef<Record<string, string>>({});
  useEffect(() => {
    const source = new EventSource("/api/jobs/events");
    source.onmessage = (e) => {
      const next = JSON.parse(e.data) as Job[];
      setJobs(next);
      const terminal = next.find(
        (item) =>
          item.project_id === project?.id &&
          ["COMPLETED", "FAILED", "PARTIAL_SUCCESS"].includes(item.status),
      );
      if (terminal && terminalJobs.current[terminal.id] !== terminal.status) {
        terminalJobs.current[terminal.id] = terminal.status;
        api
          .project(terminal.project_id)
          .then((full) => {
            const activeProteinId =
              selectedProteinId &&
              full.proteins.some((item) => item.id === selectedProteinId)
                ? selectedProteinId
                : full.proteins[0]?.id;
            setProject(full);
            setSelectedProteinId(activeProteinId);
            setActiveGrid((current) =>
              current
                ? full.grids.find((x) => x.id === current.id) || full.grids[0]
                : undefined,
            );
            setVisiblePockets(
              new Set(
                full.pockets
                  .filter((item) => item.protein_id === activeProteinId)
                  .map((x) => x.id),
              ),
            );
          })
          .catch(() => {});
      }
    };
    return () => source.close();
  }, [project?.id, selectedProteinId]);
  const act = async (fn: () => Promise<unknown>, success?: string) => {
    setBusy(true);
    setNotice(undefined);
    try {
      await fn();
      await refresh();
      if (success) setNotice({ type: "info", text: success });
    } catch (e) {
      setNotice({
        type: "error",
        text: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setBusy(false);
    }
  };
  const newProject = async () => {
    const name = prompt("Project name", "Docking study");
    if (!name) return;
    setBusy(true);
    setNotice(undefined);
    try {
      const item = await api.createProject(name);
      await refresh(item.id);
      setNotice({ type: "info", text: "Project created" });
    } catch (e) {
      setNotice({
        type: "error",
        text: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setBusy(false);
    }
  };
  const selectProject = async (id: string) => {
    if (!id) {
      setProject(undefined);
      setSelectedProteinId(undefined);
      setActiveGrid(undefined);
      setVisiblePockets(new Set());
      localStorage.removeItem("mds.projectId");
      return;
    }
    setBusy(true);
    setNotice(undefined);
    try {
      const full = await api.project(id);
      const activeProteinId = full.proteins[0]?.id;
      setProject(full);
      setSelectedProteinId(activeProteinId);
      localStorage.setItem("mds.projectId", full.id);
      setActiveGrid(
        full.grids.find(
          (item) =>
            !item.pocket_id ||
            full.pockets.some(
              (pocket) =>
                pocket.id === item.pocket_id &&
                pocket.protein_id === activeProteinId,
            ),
        ),
      );
      setVisiblePockets(
        new Set(
          full.pockets
            .filter((item) => item.protein_id === activeProteinId)
            .map((x) => x.id),
        ),
      );
    } catch (e) {
      setNotice({
        type: "error",
        text: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setBusy(false);
    }
  };
  const selectProtein = (id: string) => {
    setSelectedProteinId(id);
    setSelectedResidues(new Set());
    setActiveGrid(undefined);
    setVisiblePockets(
      new Set(
        project?.pockets
          .filter((item) => item.protein_id === id)
          .map((x) => x.id) || [],
      ),
    );
  };
  return (
    <div className="flex h-screen flex-col overflow-hidden bg-ink text-slate-100">
      <header className="flex h-16 shrink-0 items-center border-b border-line bg-[#091523] px-5">
        <div className="flex min-w-72 items-center gap-3">
          <div className="grid h-9 w-9 place-items-center rounded-xl bg-cyan/10 ring-1 ring-cyan/30">
            <Hexagon className="h-5 w-5 text-cyan" />
          </div>
          <div>
            <div className="text-sm font-semibold tracking-wide">iScreeM</div>
            <div className="text-[10px] uppercase tracking-[.22em] text-slate-500">
              Structure • Dock • Analyze
            </div>
          </div>
        </div>
        <div className="mx-auto flex gap-1">
          {tabs.map((item) => {
            const queued = jobs.some(
                (job) => job.status === "QUEUED" && !job.cancel_requested,
              ),
              running = jobs.some(
                (job) =>
                  ["PREPARING", "RUNNING", "POSTPROCESSING"].includes(
                    job.status,
                  ) && !job.cancel_requested,
              );
            const statusColor =
              item.id === "jobs"
                ? queued
                  ? "#f87171"
                  : running
                    ? "#facc15"
                    : undefined
                : undefined;
            return (
              <button
                key={item.id}
                className={`tab ${tab === item.id ? "tab-active" : ""}`}
                style={statusColor ? { color: statusColor } : undefined}
                onClick={() => setTab(item.id)}
              >
                <item.icon />
                {item.label}
              </button>
            );
          })}
        </div>
        <div className="flex min-w-72 justify-end">
          <ToolSummary tools={tools} />
        </div>
      </header>
      {notice && (
        <div
          className={`mx-5 mt-3 flex items-center justify-between rounded-lg border px-4 py-2 text-sm ${notice.type === "error" ? "border-red-500/30 bg-red-500/10 text-red-200" : "border-cyan/30 bg-cyan/10 text-cyan"}`}
        >
          <span className="flex items-center gap-2">
            {notice.type === "error" ? (
              <AlertTriangle className="h-4 w-4" />
            ) : (
              <CircleDotDashed className="h-4 w-4" />
            )}
            {notice.text}
          </span>
          <button onClick={() => setNotice(undefined)}>
            <X className="h-4 w-4" />
          </button>
        </div>
      )}
      <main className="min-h-0 flex-1 overflow-hidden p-5">
        <div className="grid h-full min-h-0 grid-cols-[260px_minmax(480px,1fr)_320px] grid-rows-[minmax(320px,3fr)_minmax(160px,2fr)] gap-4">
          <aside className="panel row-span-1 min-h-0 overflow-y-auto">
            <div className="panel-head sticky top-0 z-10 bg-panel">
              <span>Workspace</span>
              <button className="icon-btn" onClick={newProject}>
                <FolderPlus />
              </button>
            </div>
            <div className="p-3">
              <select
                className="input"
                value={project?.id || ""}
                onChange={(e) => selectProject(e.target.value)}
              >
                <option value="">Select project…</option>
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
              {project && (
                <ObjectTree
                  project={project}
                  selectedProteinId={protein?.id}
                  setSelectedProteinId={selectProtein}
                  activeGrid={activeGrid}
                  setActiveGrid={setActiveGrid}
                  visibleStructureLigands={visibleStructureLigands}
                  setVisibleStructureLigands={setVisibleStructureLigands}
                  onFocusGrid={(grid) =>
                    setFocusRequest({ kind: "grid", grid, nonce: Date.now() })
                  }
                  onFocusLigand={(owner, ligand) => {
                    selectProtein(owner.id);
                    setFocusRequest({
                      kind: "ligand",
                      ligand,
                      nonce: Date.now(),
                    });
                  }}
                />
              )}
            </div>
          </aside>
          <Viewer
            protein={protein}
            grid={activeGrid}
            pockets={project?.pockets || []}
            visiblePockets={visiblePockets}
            visibleStructureLigands={visibleStructureLigands}
            dockingPoses={selectedDockingPoses}
            dockingPoseFocus={dockingPoseFocus}
            focusRequest={focusRequest}
            selectedResidues={selectedResidues}
            onSelectResidue={toggleSelectedResidue}
            onClearResidues={clearSelectedResidues}
          />
          <aside className="panel min-h-0 overflow-y-auto">
            <RightPanel
              tab={tab}
              project={project}
              protein={protein}
              tools={tools}
              grid={activeGrid}
              setGrid={setActiveGrid}
              busy={busy}
              act={act}
              selectedLigands={selectedLigands}
            />
          </aside>
          <section className="panel col-span-3 min-h-0 overflow-y-auto overscroll-contain">
            <BottomPanel
              tab={tab}
              project={project}
              protein={protein}
              tools={tools}
              selectedProteinId={protein?.id}
              setSelectedProteinId={selectProtein}
              activeGrid={activeGrid}
              jobs={jobs}
              selectedLigands={selectedLigands}
              setSelectedLigands={setSelectedLigands}
              selectedResidues={selectedResidues}
              setSelectedResidues={setSelectedResidues}
              visiblePockets={visiblePockets}
              setVisiblePockets={setVisiblePockets}
              setActiveGrid={setActiveGrid}
              act={act}
              dockingResults={dockingResults}
              setDockingResults={setDockingResults}
              selectedResultKeys={selectedResultKeys}
              setSelectedResultKeys={setSelectedResultKeys}
              visibleResultKeys={visibleResultKeys}
              setVisibleResultKeys={setVisibleResultKeys}
              activePose={activePose}
              setActivePose={setActivePose}
              onPoseFocus={(row) => {
                if (row.protein_id && row.protein_id !== protein?.id)
                  selectProtein(row.protein_id);
                setActivePose(row);
                setDockingPoseFocus({ key: row.key, nonce: Date.now() });
              }}
              onRefreshResults={() =>
                project &&
                api.dockingResults(project.id).then(setDockingResults)
              }
              onNotice={(text) => setNotice({ type: "error", text })}
            />
          </section>
        </div>
      </main>
    </div>
  );
}

function ToolSummary({ tools }: { tools: Record<string, Tool> }) {
  const cli = ["smina", "gnina", "p2rank"];
  const ready = cli.filter((x) => tools[x]?.available).length;
  return (
    <div className="group relative">
      <button className="flex items-center gap-2 rounded-lg border border-line bg-panel px-3 py-2 text-xs text-slate-300">
        <Server className="h-4 w-4 text-mint" />
        {ready}/{cli.length} engines ready
      </button>
      <div className="invisible absolute right-0 top-11 z-50 w-80 rounded-xl border border-line bg-panel p-3 opacity-0 shadow-2xl transition group-hover:visible group-hover:opacity-100">
        <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">
          System tools
        </div>
        {Object.entries(tools).map(([key, t]) => (
          <div
            key={key}
            className="flex items-start gap-2 border-t border-line/60 py-2 text-xs"
          >
            <span
              className={`mt-1 h-2 w-2 rounded-full ${t.available ? "bg-mint" : "bg-red-400"}`}
            />
            <div>
              <div className="font-mono">{key}</div>
              <div className="max-w-64 truncate text-slate-500">
                {t.version || t.error}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function ObjectTree({
  project,
  selectedProteinId,
  setSelectedProteinId,
  activeGrid,
  setActiveGrid,
  visibleStructureLigands,
  setVisibleStructureLigands,
  onFocusGrid,
  onFocusLigand,
}: {
  project: Project;
  selectedProteinId?: string;
  setSelectedProteinId: (id: string) => void;
  activeGrid?: Grid;
  setActiveGrid: (x: Grid | undefined) => void;
  visibleStructureLigands: Set<string>;
  setVisibleStructureLigands: (x: Set<string>) => void;
  onFocusGrid: (grid: Grid) => void;
  onFocusLigand: (
    protein: Project["proteins"][number],
    ligand: StructureLigand,
  ) => void;
}) {
  return (
    <div className="mt-5 text-sm">
      <TreeGroup title="Receptors" count={project.proteins.length}>
        {project.proteins.map((protein) => (
          <ReceptorTreeItem
            key={protein.id}
            project={project}
            protein={protein}
            selected={selectedProteinId === protein.id}
            onSelect={() => setSelectedProteinId(protein.id)}
            activeGrid={activeGrid}
            setActiveGrid={setActiveGrid}
            visibleStructureLigands={visibleStructureLigands}
            setVisibleStructureLigands={setVisibleStructureLigands}
            onFocusGrid={onFocusGrid}
            onFocusLigand={onFocusLigand}
          />
        ))}
      </TreeGroup>
    </div>
  );
}
function ReceptorTreeItem({
  project,
  protein,
  selected,
  onSelect,
  activeGrid,
  setActiveGrid,
  visibleStructureLigands,
  setVisibleStructureLigands,
  onFocusGrid,
  onFocusLigand,
}: {
  project: Project;
  protein: Project["proteins"][number];
  selected: boolean;
  onSelect: () => void;
  activeGrid?: Grid;
  setActiveGrid: (x: Grid | undefined) => void;
  visibleStructureLigands: Set<string>;
  setVisibleStructureLigands: (x: Set<string>) => void;
  onFocusGrid: (grid: Grid) => void;
  onFocusLigand: (
    protein: Project["proteins"][number],
    ligand: StructureLigand,
  ) => void;
}) {
  const [open, setOpen] = useState(selected);
  useEffect(() => {
    if (selected) setOpen(true);
  }, [selected]);
  const pocketIds = new Set(
    project.pockets
      .filter((pocket) => pocket.protein_id === protein.id)
      .map((pocket) => pocket.id),
  );
  const grids = project.grids.filter((grid) =>
    grid.pocket_id ? pocketIds.has(grid.pocket_id) : selected,
  );
  const ligands = protein.stats.hetero_molecules || [];
  return (
    <div className="mb-2">
      <div
        className={`flex items-center rounded-md ${selected ? "bg-cyan/10 text-cyan" : ""}`}
      >
        <button
          type="button"
          className="grid h-7 w-7 shrink-0 place-items-center"
          aria-label={`${open ? "Collapse" : "Expand"} ${protein.name}`}
          onClick={() => setOpen((value) => !value)}
        >
          <ChevronRight
            className={`h-3.5 w-3.5 transition-transform ${open ? "rotate-90" : ""}`}
          />
        </button>
        <button className="tree-item min-w-0 flex-1 pl-0" onClick={onSelect}>
          <span className="h-2 w-2 shrink-0 rounded-full bg-cyan" />
          <span className="truncate">{protein.name}</span>
          <span className="ml-auto text-[10px] text-slate-600">
            {protein.stats.format?.toUpperCase()}
          </span>
        </button>
      </div>
      {open && (
        <div className="ml-3 mt-2 space-y-3 border-l border-line pl-3">
          <TreeGroup title="Ligand" count={ligands.length}>
            {ligands.map((ligand) => {
              const key = structureLigandKey(ligand),
                visible = visibleStructureLigands.has(key),
                location =
                  ligand.chain && ligand.residue_number
                    ? `${ligand.chain}:${ligand.residue_number}`
                    : `${ligand.atom_count} atoms`;
              return (
                <div className="tree-item w-full" key={key}>
                  <button
                    className="flex min-w-0 flex-1 items-center gap-2 text-left"
                    onClick={() => {
                      onSelect();
                      const next = new Set(visibleStructureLigands);
                      next.has(key) ? next.delete(key) : next.add(key);
                      setVisibleStructureLigands(next);
                    }}
                  >
                    <span
                      className={`h-2 w-2 shrink-0 rounded-full ${visible ? "bg-amber" : "bg-slate-700"}`}
                    />
                    <span className="truncate">{ligand.name}</span>
                    <span className="ml-auto text-[10px] text-slate-600">
                      {location}
                    </span>
                  </button>
                  <button
                    className="icon-btn h-6 w-6 shrink-0"
                    title={`Focus ${ligand.name} and residues within 10 Å`}
                    onClick={() => {
                      onSelect();
                      onFocusLigand(protein, ligand);
                    }}
                  >
                    <Focus />
                  </button>
                </div>
              );
            })}
          </TreeGroup>
          <TreeGroup title="Grid" count={grids.length}>
            {grids.map((grid) => (
              <div className="tree-item w-full" key={grid.id}>
                <button
                  className={`flex min-w-0 flex-1 items-center gap-2 text-left ${activeGrid?.id === grid.id ? "text-cyan" : ""}`}
                  onClick={() => {
                    onSelect();
                    setActiveGrid(
                      activeGrid?.id === grid.id ? undefined : grid,
                    );
                  }}
                >
                  <Boxes className="h-3.5 w-3.5 shrink-0" />
                  <span className="truncate">{grid.name}</span>
                </button>
                <button
                  className="icon-btn h-6 w-6 shrink-0"
                  title={`Focus ${grid.name} and residues within 10 Å`}
                  onClick={() => {
                    onSelect();
                    onFocusGrid(grid);
                  }}
                >
                  <Focus />
                </button>
              </div>
            ))}
          </TreeGroup>
        </div>
      )}
    </div>
  );
}
function TreeGroup({
  title,
  count,
  children,
}: {
  title: string;
  count: number;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(true);
  return (
    <div>
      <button
        type="button"
        aria-expanded={open}
        className="mb-2 flex w-full items-center gap-2 text-left text-[10px] font-semibold uppercase tracking-[.18em] text-slate-500"
        onClick={() => setOpen((value) => !value)}
      >
        <ChevronRight
          className={`h-3 w-3 transition-transform ${open ? "rotate-90" : ""}`}
        />
        {title}
        <span className="ml-auto rounded bg-slate-800 px-1.5 py-0.5 font-mono">
          {count}
        </span>
      </button>
      {open && <div className="space-y-1 pl-2">{children}</div>}
    </div>
  );
}

function RightPanel({
  tab,
  project,
  protein,
  tools,
  grid,
  setGrid,
  busy,
  act,
  selectedLigands,
}: {
  tab: Tab;
  project?: Project;
  protein?: Project["proteins"][number];
  tools: Record<string, Tool>;
  grid?: Grid;
  setGrid: (x: Grid | undefined) => void;
  busy: boolean;
  act: (fn: () => Promise<unknown>, msg?: string) => void;
  selectedLigands: Set<string>;
}) {
  if (tab === "licenses")
    return (
      <>
        <div className="panel-head">Open-source software</div>
        <div className="space-y-4 p-4 text-xs leading-5 text-slate-400">
          <p>
            Original iScreeM code is available under the MIT License. The
            projects listed below remain subject to their own licenses and are
            not relicensed under MIT.
          </p>
          <div className="rounded-lg border border-amber/20 bg-amber/5 p-3 text-amber-100/80">
            This source repository does not distribute smina, gnina, Redis, or
            P2Rank executables or P2Rank models. gnina's official pre-built
            executable targets Linux/WSL and is disabled on macOS.
          </div>
          <p>
            This screen lists the major direct components. Transitive
            dependencies may carry additional notices; follow each source link
            for the authoritative license text.
          </p>
        </div>
      </>
    );
  if (!project)
    return (
      <div className="empty-panel m-4">
        Create or select a project to begin.
      </div>
    );
  if (tab === "protein")
    return (
      <>
        <div className="panel-head">Grid Inspector</div>
        <div className="p-4">
          <GridEditor
            grid={grid}
            pocket={project.pockets.find((item) => item.id === grid?.pocket_id)}
            onChange={setGrid}
          />
          {grid && (
            <div className="mt-5 grid grid-cols-2 gap-2">
              <button
                disabled={busy}
                className="btn-primary"
                onClick={() =>
                  act(
                    () =>
                      api.patchGrid(grid.id, {
                        name: grid.name,
                        center: grid.center,
                        size: grid.size,
                      }),
                    "Grid saved",
                  )
                }
              >
                Save grid
              </button>
              <button
                disabled={busy}
                className="btn-secondary text-red-300"
                onClick={() => {
                  if (window.confirm(`Delete grid “${grid.name}”?`))
                    act(async () => {
                      await api.deleteGrid(grid.id);
                      setGrid(undefined);
                    }, "Grid deleted");
                }}
              >
                <Trash2 />
                Delete grid
              </button>
            </div>
          )}
        </div>
      </>
    );
  if (tab === "ligands") return <LigandInput project={project} act={act} />;
  if (tab === "setup")
    return (
      <DockingSetup
        project={project}
        protein={protein}
        grid={grid}
        tools={tools}
        selectedLigands={selectedLigands}
        act={act}
      />
    );
  if (tab === "results")
    return (
      <>
        <div className="panel-head">Result Display</div>
        <div className="p-4 text-sm text-slate-400">
          Completed docking poses are retained under each job directory. Score
          columns are never synthesized when the engine does not report them.
        </div>
      </>
    );
  return (
    <>
      <div className="panel-head">Queue Health</div>
      <div className="p-4">
        <StatusLine label="Redis / RQ" ok={!!tools.redis?.available} />
        <StatusLine
          label="Background persistence"
          ok={!!tools.redis?.available}
        />
        <StatusLine label="Browser reconnect" ok />
      </div>
    </>
  );
}

const normalizeColumn = (value: string) =>
  value
    .normalize("NFKC")
    .toLowerCase()
    .replace(/[\s_\-().]/g, "");
const parseDelimited = (text: string) => {
  const firstLine = text.split(/\r?\n/)[0] || "";
  const delimiter =
    (firstLine.match(/\t/g) || []).length > (firstLine.match(/,/g) || []).length
      ? "\t"
      : ",";
  const rows: string[][] = [];
  let row: string[] = [],
    cell = "",
    quoted = false;
  for (let i = 0; i < text.length; i++) {
    const char = text[i],
      next = text[i + 1];
    if (char === '"' && quoted && next === '"') {
      cell += '"';
      i++;
      continue;
    }
    if (char === '"') {
      quoted = !quoted;
      continue;
    }
    if (char === delimiter && !quoted) {
      row.push(cell.trim());
      cell = "";
      continue;
    }
    if ((char === "\n" || char === "\r") && !quoted) {
      if (char === "\r" && next === "\n") i++;
      row.push(cell.trim());
      if (row.some((value) => value.length)) rows.push(row);
      row = [];
      cell = "";
      continue;
    }
    cell += char;
  }
  if (cell.length || row.length) {
    row.push(cell.trim());
    if (row.some((value) => value.length)) rows.push(row);
  }
  return rows;
};
const extractLigands = (text: string, filename = "") => {
  const rows = parseDelimited(text);
  const header = rows[0]?.map(normalizeColumn) || [];
  const smilesIndex = header.findIndex(
    (value) => value === "smiles" || value.includes("smiles"),
  );
  const nameIndex = header.findIndex(
    (value) =>
      [
        "ligand",
        "ligandname",
        "name",
        "mole",
        "molecule",
        "moleculename",
        "compound",
        "compoundname",
        "title",
        "id",
        "code",
      ].includes(value) ||
      value.includes("ligand") ||
      value.includes("molecule"),
  );
  if (filename.toLowerCase().endsWith(".csv") && smilesIndex >= 0)
    return rows
      .slice(1)
      .map((row, index) => ({
        smiles: (row[smilesIndex] || "").trim(),
        name:
          (nameIndex >= 0 ? row[nameIndex] : "").trim() ||
          `Ligand ${index + 1}`,
      }))
      .filter((item) => item.smiles);
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line, index) => {
      const [smiles, ...name] = line.split(/\s+/);
      return { smiles, name: name.join(" ") || `Ligand ${index + 1}` };
    })
    .filter((item) => item.smiles);
};
function LigandInput({
  project,
  act,
}: {
  project: Project;
  act: (fn: () => Promise<unknown>, msg?: string) => void;
}) {
  const [text, setText] = useState(
      "CC(=O)Oc1ccccc1C(=O)O aspirin\nCn1cnc2n(C)c(=O)n(C)c(=O)c12 caffeine",
    ),
    [file, setFile] = useState<File>(),
    [showKetcher, setShowKetcher] = useState(false);
  const importItems = (
    items: { smiles: string; name?: string }[],
    message = "Ligands imported",
  ) => {
    if (!items.length) {
      window.alert("No SMILES values were found.");
      return;
    }
    act(() => api.importLigands(project.id, items), message);
  };
  const importFile = async () => {
    if (!file) return;
    const items = extractLigands(await file.text(), file.name);
    importItems(items, `${items.length} ligands imported`);
  };
  return (
    <>
      <div className="panel-head">SMILES / CSV Import</div>
      <div className="space-y-4 p-4">
        <button
          className="btn-secondary w-full justify-center"
          onClick={() => setShowKetcher(true)}
        >
          <FlaskConical />
          Draw with Ketcher
        </button>
        <p className="text-xs leading-5 text-slate-500">
          Ketcherで描画した構造はSMILESへ変換し、名前とともにLigand
          Libraryへ直接追加できます。テキストは1行1分子（
          <span className="font-mono text-slate-400">SMILES [name]</span>
          ）。CSVはSMILES列（smiles / SMILES /
          canonical_smiles等）を自動検出し、Ligand・name・molecule等から名前を読み込みます。
        </p>
        <textarea
          className="input h-32 resize-none font-mono text-xs"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <button
          className="btn-primary w-full"
          onClick={() => importItems(extractLigands(text))}
        >
          <Upload />
          Import text
        </button>
        <div className="rounded-lg border border-line bg-ink/30 p-3">
          <input
            type="file"
            accept=".csv,.smi,.smiles,.txt,text/csv,text/plain"
            onChange={(e) => setFile(e.target.files?.[0])}
          />
          <div className="mt-2 flex items-center justify-between gap-3">
            <span className="truncate text-xs text-slate-500">
              {file?.name || "CSV / SMILES file not selected"}
            </span>
            <button
              className="btn-secondary"
              disabled={!file}
              onClick={importFile}
            >
              Import file
            </button>
          </div>
        </div>
      </div>
      {showKetcher && (
        <Suspense
          fallback={
            <div className="fixed inset-0 z-[100] grid place-items-center bg-black/75 text-sm text-slate-300">
              Loading Ketcher…
            </div>
          }
        >
          <KetcherDialog
            open
            onClose={() => setShowKetcher(false)}
            onImport={(smiles, name) =>
              importItems([{ smiles, name }], `${name} imported from Ketcher`)
            }
          />
        </Suspense>
      )}
    </>
  );
}

function DockingSetup({
  project,
  protein,
  grid,
  tools,
  selectedLigands,
  act,
}: {
  project: Project;
  protein?: Project["proteins"][number];
  grid?: Grid;
  tools: Record<string, Tool>;
  selectedLigands: Set<string>;
  act: (fn: () => Promise<unknown>, msg?: string) => void;
}) {
  const [engine, setEngine] = useState<"smina" | "gnina">("smina"),
    [exhaustiveness, setExhaustiveness] = useState(8),
    [modes, setModes] = useState(3),
    [withH2O, setWithH2O] = useState(false),
    [uploads, setUploads] = useState<DockingUpload[]>([]);
  const uploadInput = useRef<HTMLInputElement>(null);
  const uploadedCount = uploads.reduce(
    (total, batch) => total + batch.ligands.length,
    0,
  );
  const runnable =
    !!tools[engine]?.available &&
    !!protein?.prepared_path &&
    selectedLigands.size + uploadedCount > 0;
  const uploadFile = (file?: File) => {
    if (!file) return;
    act(async () => {
      const uploaded = await api.uploadDockingLigands(project.id, file);
      setUploads((current) => [...current, uploaded]);
      if (uploadInput.current) uploadInput.current.value = "";
    }, `${file.name} converted to 3D docking inputs`);
  };
  return (
    <>
      <div className="panel-head">Docking Configuration</div>
      <div className="space-y-4 p-4">
        <div>
          <div className="label">Engine</div>
          <div className="grid grid-cols-2 gap-2">
            {(["smina", "gnina"] as const).map((x) => (
              <button
                key={x}
                title={
                  tools[x]?.available
                    ? `${tools[x]?.version || x} · ${tools[x]?.path || "PATH"}`
                    : tools[x]?.error || `${x} is unavailable`
                }
                disabled={!tools[x]?.available}
                onClick={() => setEngine(x)}
                className={`engine ${engine === x ? "engine-active" : ""}`}
              >
                <span>{x}</span>
                <small>
                  {tools[x]?.available
                    ? "ready"
                    : x === "gnina" && tools[x]?.error?.includes("macOS")
                      ? "Linux only"
                      : "unavailable"}
                </small>
              </button>
            ))}
          </div>
        </div>
        <Field
          label="Receptor"
          value={
            protein
              ? `${protein.name}${protein.prepared_path ? " · Prepared PDB" : " · Preparation required"}`
              : "Select a receptor"
          }
        />
        <label className="flex items-center gap-2 rounded-lg border border-line bg-ink/30 px-3 py-2 text-xs text-slate-300">
          <input
            type="checkbox"
            checked={withH2O}
            onChange={(event) => setWithH2O(event.target.checked)}
          />
          <span>
            <span className="font-semibold text-slate-200">with H2O</span> ·
            retain water molecules with a polar contact to the receptor
          </span>
        </label>
        <Field
          label="Grid"
          value={grid?.name || "Global search without grid"}
        />
        <div>
          <div className="label">Ligands</div>
          <div className="grid grid-cols-2 gap-2">
            <div className="input text-slate-300">
              {selectedLigands.size} library + {uploadedCount} uploaded
            </div>
            <button
              className="btn-secondary w-full justify-center"
              onClick={() => uploadInput.current?.click()}
            >
              <Upload />
              Upload SDF / CSV / SMI
            </button>
            <input
              ref={uploadInput}
              className="hidden"
              type="file"
              accept=".sdf,.csv,.smi,.smiles,chemical/x-mdl-sdfile,text/csv,text/plain"
              onChange={(event) => uploadFile(event.target.files?.[0])}
            />
          </div>
          {uploads.length > 0 && (
            <div className="mt-2 text-[10px] text-slate-500">
              Job-only 3D inputs:{" "}
              {uploads
                .map(
                  (batch) => `${batch.original_name} (${batch.ligands.length})`,
                )
                .join(", ")}
            </div>
          )}
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <div className="label">Exhaustiveness</div>
            <input
              className="input font-mono"
              type="number"
              min="1"
              value={exhaustiveness}
              onChange={(e) => setExhaustiveness(+e.target.value)}
            />
          </div>
          <div>
            <div className="label">Modes</div>
            <input
              className="input font-mono"
              type="number"
              min="1"
              value={modes}
              onChange={(e) => setModes(+e.target.value)}
            />
          </div>
        </div>
        <div className="rounded-lg border border-amber/20 bg-amber/5 p-3 text-xs text-amber-200/80">
          Without a selected grid, Global search without grid covers the
          complete receptor with a 4 Å margin. Organic HETATM records are
          excluded from the docking receptor. With H2O retains only water
          molecules within 3.6 Å of receptor N/O/S atoms. Uploaded ligands are
          converted to 3D for this docking run and are not added to Ligand
          Preparation. Library ligands that have not been prepared are
          automatically normalized and converted to 3D in the docking job.
        </div>
        <button
          disabled={!runnable}
          className="btn-primary w-full"
          onClick={() =>
            protein &&
            act(
              () =>
                api.runDocking(project.id, {
                  name: `${engine} docking · ${protein.name}${grid ? "" : " · global"}`,
                  protein_id: protein.id,
                  ...(grid ? { grid_id: grid.id } : {}),
                  ligand_ids: [...selectedLigands],
                  upload_ids: uploads.map((batch) => batch.upload_id),
                  with_h2o: withH2O,
                  engine,
                  exhaustiveness,
                  num_modes: modes,
                  ...(engine === "gnina" ? { cnn_scoring: "rescore" } : {}),
                }),
              "Docking job queued",
            )
          }
        >
          <Play />
          Run docking
        </button>
      </div>
    </>
  );
}
function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="label">{label}</div>
      <div className="input text-slate-300">{value}</div>
    </div>
  );
}
function StatusLine({ label, ok }: { label: string; ok: boolean }) {
  return (
    <div className="flex items-center justify-between border-b border-line py-3 text-sm">
      <span className="text-slate-400">{label}</span>
      <span className={ok ? "text-mint" : "text-red-400"}>
        {ok ? "Ready" : "Unavailable"}
      </span>
    </div>
  );
}

function BottomPanel(props: {
  tab: Tab;
  project?: Project;
  protein?: Project["proteins"][number];
  selectedProteinId?: string;
  setSelectedProteinId: (id: string) => void;
  activeGrid?: Grid;
  jobs: Job[];
  tools: Record<string, Tool>;
  selectedLigands: Set<string>;
  setSelectedLigands: (x: Set<string>) => void;
  selectedResidues: Set<string>;
  setSelectedResidues: (x: Set<string>) => void;
  visiblePockets: Set<string>;
  setVisiblePockets: (x: Set<string>) => void;
  setActiveGrid: (x: Grid | undefined) => void;
  act: (fn: () => Promise<unknown>, msg?: string) => void;
  dockingResults: DockingResult[];
  setDockingResults: (x: DockingResult[]) => void;
  selectedResultKeys: Set<string>;
  setSelectedResultKeys: (x: Set<string>) => void;
  visibleResultKeys: Set<string>;
  setVisibleResultKeys: (x: Set<string>) => void;
  activePose?: DockingResult;
  setActivePose: (x: DockingResult | undefined) => void;
  onPoseFocus: (row: DockingResult) => void;
  onRefreshResults: () => void;
  onNotice: (text: string) => void;
}) {
  const { tab, project, jobs } = props;
  if (tab === "protein") return <ProteinBottom {...props} />;
  if (tab === "ligands") return <LigandBottom {...props} />;
  if (tab === "jobs") return <JobsBottom jobs={jobs} act={props.act} />;
  if (tab === "licenses") return <LicensesBottom />;
  if (tab === "results")
    return (
      <ResultsBottomEnhanced
        project={project}
        tools={props.tools}
        results={props.dockingResults}
        setResults={props.setDockingResults}
        selectedKeys={props.selectedResultKeys}
        setSelectedKeys={props.setSelectedResultKeys}
        visibleKeys={props.visibleResultKeys}
        setVisibleKeys={props.setVisibleResultKeys}
        activePose={props.activePose}
        setActivePose={props.setActivePose}
        onPoseFocus={props.onPoseFocus}
        refresh={props.onRefreshResults}
        onNotice={props.onNotice}
      />
    );
  return (
    <div className="p-5 text-sm text-slate-500">
      Select a receptor and prepared ligands. Choose a saved grid for a local
      search, or leave it unselected for Global search without grid.
    </div>
  );
}

function ProteinBottom({
  project,
  protein,
  tools,
  selectedProteinId,
  setSelectedProteinId,
  activeGrid,
  selectedResidues,
  setSelectedResidues,
  visiblePockets,
  setVisiblePockets,
  setActiveGrid,
  act,
}: {
  project?: Project;
  protein?: Project["proteins"][number];
  tools: Record<string, Tool>;
  selectedProteinId?: string;
  setSelectedProteinId: (id: string) => void;
  activeGrid?: Grid;
  selectedResidues: Set<string>;
  setSelectedResidues: (x: Set<string>) => void;
  visiblePockets: Set<string>;
  setVisiblePockets: (x: Set<string>) => void;
  setActiveGrid: (x: Grid | undefined) => void;
  act: (fn: () => Promise<unknown>, msg?: string) => void;
}) {
  const upload = (file?: File) => {
    if (file && project)
      act(async () => {
        const added = (await api.uploadProtein(project.id, file)) as {
          id: string;
        };
        setSelectedProteinId(added.id);
      }, "Protein uploaded and parsed");
  };
  const remove = () => {
    if (
      !project ||
      !protein ||
      !window.confirm(
        `Delete protein “${protein.name}” and its preparation/pocket files?`,
      )
    )
      return;
    act(async () => {
      await api.deleteProtein(project.id, protein.id);
      setSelectedProteinId("");
    }, "Protein and associated files deleted");
  };
  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    upload(event.dataTransfer.files?.[0]);
  };
  return (
    <>
      <div className="panel-head">
        <span>Protein & Pocket Summary</span>
        <div className="flex gap-2">
          {project && (
            <label className="btn-secondary cursor-pointer">
              <Upload />
              Add structure
              <input
                type="file"
                className="hidden"
                accept=".pdb,.ent,.cif,.mmcif"
                onChange={(e) => upload(e.target.files?.[0])}
              />
            </label>
          )}
          <button
            disabled={
              !project ||
              !protein ||
              !tools.pdbfixer?.available ||
              !tools.pdb2pqr?.available
            }
            className="btn-secondary"
            onClick={() =>
              project &&
              protein &&
              act(
                () => api.prepareReceptor(project.id, protein.id),
                "PDBFixer + PDB2PQR preparation queued",
              )
            }
          >
            <Beaker />
            Repair & prepare receptor
          </button>
          <button
            disabled={!project || !protein || !tools.p2rank?.available}
            className="btn-primary"
            onClick={() =>
              project &&
              protein &&
              act(
                () => api.predictPockets(project.id, protein.id),
                "Pocket prediction queued",
              )
            }
          >
            <Search />
            Predict pockets
          </button>
          {protein && (
            <button
              disabled={!protein}
              className="btn-secondary text-red-300"
              onClick={remove}
              title="Delete selected receptor"
            >
              <Trash2 />
              Delete
            </button>
          )}
        </div>
      </div>
      <div
        className="space-y-5 p-4"
        onDragOver={(event) => event.preventDefault()}
        onDrop={onDrop}
      >
        {protein ? (
          <>
            <div className="rounded-lg border border-cyan/20 bg-cyan/5 px-3 py-2 text-xs text-cyan/80">
              Active receptor:{" "}
              <span className="font-semibold">{protein.name}</span> ·{" "}
              {protein.stats.format?.toUpperCase()}
            </div>
            <SequencePanel
              protein={protein}
              pockets={
                project?.pockets.filter((p) => p.protein_id === protein.id) ||
                []
              }
              selectedResidues={selectedResidues}
              setSelectedResidues={setSelectedResidues}
            />
            <div>
              <div className="mb-3 flex items-center justify-between">
                <div className="text-xs font-semibold uppercase tracking-[.18em] text-slate-500">
                  Predicted pocket summary · {protein.name}
                </div>
                <div className="text-xs text-slate-600">
                  Click a pocket color or residue to inspect it in the viewer
                </div>
              </div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Rank</th>
                      <th>Pocket</th>
                      <th>Score</th>
                      <th>Probability</th>
                      <th>Pocket volume (Å³)</th>
                      <th>Estimated MW (Da)</th>
                      <th>Residues</th>
                      <th>Visible</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {project?.pockets
                      .filter((p) => p.protein_id === protein.id)
                      .map((p) => (
                        <tr key={p.id}>
                          <td>
                            <span
                              className="inline-flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-bold"
                              style={{
                                backgroundColor: pocketColorForRank(p.rank),
                              }}
                            >
                              {p.rank}
                            </span>
                          </td>
                          <td>Pocket {p.pocket_key}</td>
                          <td className="font-mono">
                            {p.score?.toFixed(3) || "—"}
                          </td>
                          <td className="font-mono">
                            {p.probability?.toFixed(3) || "—"}
                          </td>
                          <td className="font-mono" title={p.volume_method || undefined}>
                            {p.volume?.toFixed(1) || "—"}
                          </td>
                          <td
                            className="font-mono"
                            title="Estimated MW ceiling = 0.45 × pocket volume"
                          >
                            {p.estimated_max_mw?.toFixed(1) || "—"}
                          </td>
                          <td>{p.residues.slice(0, 4).join(", ") || "—"}</td>
                          <td>
                            <input
                              type="checkbox"
                              checked={visiblePockets.has(p.id)}
                              onChange={() => {
                                const next = new Set(visiblePockets);
                                next.has(p.id)
                                  ? next.delete(p.id)
                                  : next.add(p.id);
                                setVisiblePockets(next);
                              }}
                            />
                          </td>
                          <td>
                            <div className="flex gap-2">
                              <button
                                className="btn-secondary"
                                onClick={() =>
                                  project &&
                                  act(async () => {
                                    const g = (await api.createGrid(
                                      project.id,
                                      p.id,
                                      `Pocket ${p.rank} grid`,
                                    )) as Grid;
                                    setActiveGrid(g);
                                  }, "Grid created")
                                }
                              >
                                Create grid
                              </button>
                              <button
                                className="btn-secondary text-red-300"
                                title="Delete pocket"
                                onClick={() =>
                                  project &&
                                  window.confirm(
                                    `Delete pocket ${p.rank} and its grids?`,
                                  ) &&
                                  act(async () => {
                                    await api.deletePocket(project.id, p.id);
                                    const next = new Set(visiblePockets);
                                    next.delete(p.id);
                                    setVisiblePockets(next);
                                    if (activeGrid?.pocket_id === p.id)
                                      setActiveGrid(undefined);
                                  }, "Pocket and associated grids deleted")
                                }
                              >
                                <Trash2 />
                              </button>
                            </div>
                          </td>
                        </tr>
                      ))}
                  </tbody>
                </table>
                {!project?.pockets.some((p) => p.protein_id === protein.id) && (
                  <div className="py-8 text-center text-sm text-slate-500">
                    No pocket predictions for this receptor
                  </div>
                )}
              </div>
            </div>
          </>
        ) : (
          <div
            className="empty-panel flex min-h-36 flex-col items-center justify-center"
            onDragOver={(event) => event.preventDefault()}
            onDrop={onDrop}
          >
            <Upload className="mb-2 h-6 w-6 text-cyan" />
            <span>Drop PDB, CIF, or mmCIF here</span>
            <span className="mt-1 text-[10px] text-slate-600">
              or use Add structure
            </span>
          </div>
        )}
      </div>
    </>
  );
}

function SequencePanel({
  protein,
  pockets,
  selectedResidues,
  setSelectedResidues,
}: {
  protein: Project["proteins"][number];
  pockets: Pocket[];
  selectedResidues: Set<string>;
  setSelectedResidues: (x: Set<string>) => void;
}) {
  const sequences =
    protein.stats.sequences ||
    protein.stats.chains.map(
      (chain) =>
        ({
          id: chain.id,
          sequence: "X".repeat(chain.residue_count),
          residues: Array.from({ length: chain.residue_count }, (_, index) => ({
            name: "UNK",
            number: String(index + 1),
            one_letter: "X",
          })),
        }) as ChainSequence,
    );
  const pocketRanks = new Map<string, number>();
  pockets.forEach((pocket) =>
    pocket.residues.forEach((value) => {
      const match = value.match(/^(.+?)_(.+)$/);
      if (match) {
        const key = `${match[1]}:${match[2]}`,
          current = pocketRanks.get(key);
        if (current === undefined || pocket.rank < current)
          pocketRanks.set(key, pocket.rank);
      }
    }),
  );
  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <div className="text-xs font-semibold uppercase tracking-[.18em] text-slate-500">
          Amino acid sequences
        </div>
        <div className="text-xs text-slate-600">
          Select residues in the sequence or viewer
        </div>
      </div>
      <div className="space-y-3">
        {sequences.map((chain) => (
          <div
            className="rounded-lg border border-line bg-ink/30 p-3"
            key={chain.id}
          >
            <div className="mb-2 flex items-center gap-2 text-xs text-slate-400">
              <span className="font-semibold text-cyan">Chain {chain.id}</span>
              <span className="font-mono text-slate-600">
                {chain.residues.length} residues
              </span>
            </div>
            <div className="overflow-x-auto pb-1">
              <div className="min-w-max font-mono text-xs">
                <div className="flex text-[9px] text-slate-600">
                  {chain.residues.map((residue, index) => (
                    <span
                      className="w-[1ch] shrink-0 text-center"
                      key={`${chain.id}-${residue.number}-label`}
                    >
                      {(index + 1) % 10 === 0 ? residue.number : ""}
                    </span>
                  ))}
                </div>
                <div className="flex">
                  {chain.residues.map((residue, index) => {
                    const key = `${chain.id}:${residue.number}`;
                    const selected = selectedResidues.has(key);
                    const rank = pocketRanks.get(key);
                    return (
                      <button
                        key={key}
                        title={`${residue.name} ${residue.number}`}
                        aria-label={`Chain ${chain.id} residue ${residue.number} ${residue.name}`}
                        className={`w-[1ch] shrink-0 rounded py-1 text-center transition ${selected ? "bg-white text-ink" : "hover:bg-cyan/20"}`}
                        style={
                          selected
                            ? undefined
                            : rank
                              ? { color: pocketColorForRank(rank) }
                              : undefined
                        }
                        onClick={() => {
                          const next = new Set(selectedResidues);
                          selected ? next.delete(key) : next.add(key);
                          setSelectedResidues(next);
                        }}
                      >
                        {residue.one_letter}
                      </button>
                    );
                  })}
                </div>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

const format3 = (value: unknown) => {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(3) : "—";
};
function EditableLigandCell({
  value,
  label,
  wide,
  onSave,
}: {
  value: string;
  label: string;
  wide?: boolean;
  onSave: (value: string) => void;
}) {
  const [editing, setEditing] = useState(false),
    [draft, setDraft] = useState(value);
  useEffect(() => {
    if (!editing) setDraft(value);
  }, [value, editing]);
  const commit = () => {
    const next = draft.trim();
    if (!next) return;
    setEditing(false);
    if (next !== value) onSave(next);
  };
  if (editing)
    return (
      <input
        autoFocus
        aria-label={label}
        className={`input py-1 font-mono text-xs ${wide ? "min-w-72" : "min-w-32"}`}
        value={draft}
        title="Enterで保存・Escでキャンセル"
        onFocus={(event) => event.currentTarget.select()}
        onChange={(event) => setDraft(event.target.value)}
        onClick={(event) => event.stopPropagation()}
        onKeyDown={(event) => {
          if (event.key === "Enter") commit();
          if (event.key === "Escape") {
            setDraft(value);
            setEditing(false);
          }
        }}
      />
    );
  return (
    <span
      className={`block cursor-text rounded px-1 py-1 hover:bg-cyan/10 ${wide ? "max-w-xl truncate font-mono" : ""}`}
      title={`${value}\nDouble-click to edit`}
      onDoubleClick={(event) => {
        event.stopPropagation();
        setDraft(value);
        setEditing(true);
      }}
    >
      {value}
    </span>
  );
}
function LigandBottom({
  project,
  selectedLigands,
  setSelectedLigands,
  act,
}: {
  project?: Project;
  selectedLigands: Set<string>;
  setSelectedLigands: (x: Set<string>) => void;
  act: (fn: () => Promise<unknown>, msg?: string) => void;
}) {
  const data = project?.ligands || [];
  const actRef = useRef(act);
  actRef.current = act;
  const update = useCallback(
    (
      ligand: Ligand,
      body: { ligand_code?: string; name?: string; smiles?: string },
      message: string,
    ) =>
      project &&
      actRef.current(
        () => api.patchLigand(project.id, ligand.id, body),
        message,
      ),
    [project],
  );
  const columns = useMemo<ColumnDef<Ligand>[]>(
    () => [
      {
        id: "select",
        enableSorting: false,
        header: () => {
          const allSelected =
            data.length > 0 && selectedLigands.size === data.length;
          const someSelected = selectedLigands.size > 0 && !allSelected;
          return (
            <input
              type="checkbox"
              aria-label="Select all ligands"
              checked={allSelected}
              ref={(node) => {
                if (node) node.indeterminate = someSelected;
              }}
              onChange={() =>
                setSelectedLigands(
                  allSelected
                    ? new Set()
                    : new Set(data.map((ligand) => ligand.id)),
                )
              }
              onClick={(event) => event.stopPropagation()}
            />
          );
        },
        cell: ({ row }) => (
          <input
            type="checkbox"
            aria-label={`Select ${row.original.ligand_code}`}
            checked={selectedLigands.has(row.original.id)}
            onChange={() => {
              const n = new Set(selectedLigands);
              n.has(row.original.id)
                ? n.delete(row.original.id)
                : n.add(row.original.id);
              setSelectedLigands(n);
            }}
            onClick={(event) => event.stopPropagation()}
          />
        ),
      },
      {
        accessorKey: "ligand_code",
        header: "Ligand ID",
        cell: ({ row }) => (
          <EditableLigandCell
            value={row.original.ligand_code}
            label="Edit ligand ID"
            onSave={(value) =>
              update(row.original, { ligand_code: value }, "Ligand ID updated")
            }
          />
        ),
      },
      {
        accessorKey: "name",
        header: "Name",
        cell: ({ row }) => (
          <EditableLigandCell
            value={row.original.name}
            label="Edit ligand name"
            onSave={(value) =>
              update(row.original, { name: value }, "Ligand name updated")
            }
          />
        ),
      },
      {
        accessorKey: "smiles",
        header: "SMILES",
        cell: ({ row }) => (
          <EditableLigandCell
            wide
            value={row.original.smiles}
            label="Edit ligand SMILES"
            onSave={(value) =>
              update(
                row.original,
                { smiles: value },
                "SMILES updated; ligand preparation is required again",
              )
            }
          />
        ),
      },
      {
        id: "mw",
        accessorFn: (row) => Number(row.descriptors.mw),
        header: "MW",
        cell: ({ row }) => format3(row.original.descriptors.mw),
      },
      {
        id: "clogp",
        accessorFn: (row) => Number(row.descriptors.clogp),
        header: "cLogP",
        cell: ({ row }) => format3(row.original.descriptors.clogp),
      },
      {
        accessorKey: "status",
        header: "Status",
        cell: ({ getValue }) => (
          <span className="badge">{String(getValue())}</span>
        ),
      },
    ],
    [data, selectedLigands, update, setSelectedLigands],
  );
  const removeSelected = () => {
    if (
      !project ||
      !selectedLigands.size ||
      !window.confirm(
        `Delete ${selectedLigands.size} selected ligand${selectedLigands.size === 1 ? "" : "s"} and their prepared files?`,
      )
    )
      return;
    act(async () => {
      await api.deleteLigands(project.id, [...selectedLigands]);
      setSelectedLigands(new Set());
    }, `${selectedLigands.size} ligands deleted`);
  };
  return (
    <>
      <div className="panel-head">
        <span>Ligand Library · {data.length}</span>
        <span className="ml-auto mr-3 text-[10px] font-normal normal-case tracking-normal text-slate-600">
          ダブルクリックで編集・Enterで保存・Escでキャンセル
        </span>
        <div className="flex gap-2">
          <button
            className="btn-secondary text-red-300"
            disabled={!project || !selectedLigands.size}
            onClick={removeSelected}
          >
            <Trash2 />
            Delete selected
          </button>
          <button
            className="btn-primary"
            disabled={!project || !selectedLigands.size}
            onClick={() =>
              project &&
              act(
                () => api.prepareLigands(project.id, [...selectedLigands]),
                "Ligand preparation queued",
              )
            }
          >
            <Play />
            Prepare selected
          </button>
        </div>
      </div>
      <DataTable data={data} columns={columns} />
    </>
  );
}

function JobsBottom({
  jobs,
  act,
}: {
  jobs: Job[];
  act: (fn: () => Promise<unknown>, msg?: string) => void;
}) {
  const columns = useMemo<ColumnDef<Job>[]>(
    () => [
      { accessorKey: "name", header: "Job" },
      { accessorKey: "kind", header: "Type" },
      {
        accessorKey: "engine",
        header: "Engine",
        cell: ({ getValue }) => String(getValue() || "—"),
      },
      {
        accessorKey: "status",
        header: "Status",
        cell: ({ getValue }) => (
          <span className={`badge status-${String(getValue()).toLowerCase()}`}>
            {String(getValue())}
          </span>
        ),
      },
      {
        id: "progress",
        header: "Progress",
        cell: ({ row }) => (
          <div className="min-w-40">
            <div className="mb-1 flex justify-between text-[10px] text-slate-500">
              <span>
                {row.original.completed_count} / {row.original.total_count}
              </span>
              <span>
                {row.original.total_count
                  ? Math.round(
                      (row.original.completed_count /
                        row.original.total_count) *
                        100,
                    )
                  : 0}
                %
              </span>
            </div>
            <div className="h-1.5 overflow-hidden rounded bg-slate-800">
              <div
                className="h-full bg-cyan"
                style={{
                  width: `${row.original.total_count ? (row.original.completed_count / row.original.total_count) * 100 : 0}%`,
                }}
              />
            </div>
          </div>
        ),
      },
      {
        id: "outcome",
        header: "Success / Failed",
        cell: ({ row }) => (
          <span className="font-mono text-xs text-slate-400">
            <span className="text-mint">{row.original.success_count}</span> /{" "}
            <span className="text-red-400">{row.original.failed_count}</span>
          </span>
        ),
      },
      {
        id: "action",
        header: "",
        cell: ({ row }) => (
          <button
            className="btn-secondary"
            disabled={["COMPLETED", "FAILED", "CANCELLED"].includes(
              row.original.status,
            )}
            onClick={() =>
              act(
                () => api.cancelJob(row.original.id),
                "Cancellation requested",
              )
            }
          >
            Cancel
          </button>
        ),
      },
    ],
    [],
  );
  return (
    <>
      <div className="panel-head">
        <span>Background Jobs</span>
        <span className="flex items-center gap-2 text-xs text-slate-500">
          <RefreshCw className="h-3 w-3" />
          Live via SSE
        </span>
      </div>
      <DataTable data={jobs} columns={columns} />
    </>
  );
}
function LicensesBottom() {
  return (
    <>
      <div className="panel-head">
        <span>Open-source software & licenses</span>
        <span className="text-xs font-normal normal-case tracking-normal text-slate-500">
          Authoritative terms are linked to each upstream project
        </span>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Software</th>
              <th>Used for</th>
              <th>License</th>
              <th>Upstream</th>
              <th>License text</th>
            </tr>
          </thead>
          <tbody>
            {ossLicenses.map((item) => (
              <tr key={item.name}>
                <td className="font-semibold text-slate-200">{item.name}</td>
                <td className="text-slate-400">{item.role}</td>
                <td className="text-slate-300">{item.license}</td>
                <td>
                  <a
                    className="inline-flex items-center gap-1 text-cyan hover:underline"
                    href={item.source}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Source
                    <ExternalLink className="h-3 w-3" />
                  </a>
                </td>
                <td>
                  <a
                    className="inline-flex items-center gap-1 text-cyan hover:underline"
                    href={item.licenseUrl}
                    target="_blank"
                    rel="noreferrer"
                  >
                    License
                    <ExternalLink className="h-3 w-3" />
                  </a>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

type ResultSortKey = string;
const scoreLabel = (key: string) =>
  key === "rmsd_lb"
    ? "RMSD l.b."
    : key === "rmsd_ub"
      ? "RMSD u.b."
      : key
          .split("_")
          .map((part) => part.toUpperCase())
          .join(" ");
function ResultsBottomEnhanced({
  project,
  tools,
  results,
  setResults,
  selectedKeys,
  setSelectedKeys,
  visibleKeys,
  setVisibleKeys,
  activePose,
  setActivePose,
  onPoseFocus,
  refresh,
  onNotice,
}: {
  project?: Project;
  tools: Record<string, Tool>;
  results: DockingResult[];
  setResults: (x: DockingResult[]) => void;
  selectedKeys: Set<string>;
  setSelectedKeys: (x: Set<string>) => void;
  visibleKeys: Set<string>;
  setVisibleKeys: (x: Set<string>) => void;
  activePose?: DockingResult;
  setActivePose: (x: DockingResult | undefined) => void;
  onPoseFocus: (row: DockingResult) => void;
  refresh: () => void;
  onNotice: (text: string) => void;
}) {
  const [sort, setSort] = useState<{
    key: ResultSortKey;
    direction: "asc" | "desc";
  }>({ key: "rank", direction: "asc" });
  const [summaryMessage, setSummaryMessage] = useState("");
  const summaryInput = useRef<HTMLInputElement>(null);
  const scoreColumns = Array.from(
    new Set(results.flatMap((row) => Object.keys(row.scores || {}))),
  )
    .filter((key) => key !== "affinity")
    .sort();
  const sortedResults = [...results].sort((left, right) => {
    const key = sort.key;
    const value = (row: DockingResult): number | string | null => {
      if (key === "rank") return row.rank;
      if (key === "receptor_name") return row.receptor_name;
      if (key === "ligand_name") return row.ligand_name;
      if (key === "pose") return row.pose;
      if (key === "affinity") return row.affinity;
      if (key === "engine") return row.engine;
      if (key === "job_name") return row.job_name;
      return row.scores?.[key.slice(6)] ?? null;
    };
    const a = value(left),
      b = value(right);
    if (a === b) return 0;
    if (a === null || a === undefined || a === "") return 1;
    if (b === null || b === undefined || b === "") return -1;
    const comparison =
      typeof a === "number" && typeof b === "number"
        ? a - b
        : String(a).localeCompare(String(b), undefined, {
            numeric: true,
            sensitivity: "base",
          });
    return sort.direction === "asc" ? comparison : -comparison;
  });
  const ordered = sortedResults.filter((row) => selectedKeys.has(row.key));
  const navigation = ordered.length ? ordered : sortedResults;
  const setSortKey = (key: ResultSortKey) =>
    setSort((current) =>
      current.key === key
        ? { key, direction: current.direction === "asc" ? "desc" : "asc" }
        : { key, direction: "asc" },
    );
  const indicator = (key: ResultSortKey) =>
    sort.key === key ? (sort.direction === "asc" ? " ↑" : " ↓") : "";
  const moveKeyboard = (delta: number) => {
    const candidates =
      selectedKeys.size > 1 && activePose && selectedKeys.has(activePose.key)
        ? navigation
        : sortedResults;
    if (!candidates.length) return;
    const rowIndex = Math.max(
      0,
      candidates.findIndex((item) => item.key === activePose?.key),
    );
    const next =
      candidates[(rowIndex + delta + candidates.length) % candidates.length];
    setActivePose(next);
  };
  const toggle = (row: DockingResult) => {
    const next = new Set(selectedKeys);
    next.has(row.key) ? next.delete(row.key) : next.add(row.key);
    setSelectedKeys(next);
    if (!next.size) setActivePose(undefined);
    else if (!next.has(activePose?.key || "")) setActivePose(row);
  };
  const toggleVisible = (row: DockingResult) => {
    const next = new Set(visibleKeys);
    next.has(row.key) ? next.delete(row.key) : next.add(row.key);
    setVisibleKeys(next);
  };
  const focusRow = (row: DockingResult) => onPoseFocus(row);
  const exportPse = async () => {
    if (!project || !selectedKeys.size) return;
    const poses = results.filter((row) => selectedKeys.has(row.key));
    const proteinId = poses[0]?.protein_id;
    if (!proteinId) return;
    try {
      const output = await api.exportPse(project.id, {
        protein_id: proteinId,
        poses: poses.map((row) => ({
          job_id: row.job_id,
          ligand_code: row.ligand_code,
          pose: row.pose,
        })),
      });
      const link = document.createElement("a");
      link.href = output.download_url;
      link.download = output.filename;
      link.click();
    } catch (error) {
      onNotice(error instanceof Error ? error.message : String(error));
    }
  };
  const exportCsv = async () => {
    if (!project || !selectedKeys.size) return;
    const poses = results
      .filter((row) => selectedKeys.has(row.key))
      .map((row) => ({
        job_id: row.job_id,
        ligand_code: row.ligand_code,
        pose: row.pose,
      }));
    try {
      const output = await api.exportDockingCsv(project.id, poses);
      const url = URL.createObjectURL(output.blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = output.filename;
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      onNotice(error instanceof Error ? error.message : String(error));
    }
  };
  const exportSdf = async () => {
    if (!project || !selectedKeys.size) return;
    const poses = results
      .filter((row) => selectedKeys.has(row.key))
      .map((row) => ({
        job_id: row.job_id,
        ligand_code: row.ligand_code,
        pose: row.pose,
      }));
    try {
      const output = await api.exportDockingSdf(project.id, poses);
      const url = URL.createObjectURL(output.blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = output.filename;
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      onNotice(error instanceof Error ? error.message : String(error));
    }
  };
  const deleteSelectedLigands = async () => {
    if (!project || !selectedKeys.size) return;
    const selected = results.filter((row) => selectedKeys.has(row.key));
    const unique = new Map(
      selected.map((row) => [
        `${row.job_id}:${row.ligand_code}`,
        { job_id: row.job_id, ligand_code: row.ligand_code },
      ]),
    );
    if (
      !unique.size ||
      !window.confirm(
        `Remove ${unique.size} selected ligand${unique.size === 1 ? "" : "s"} from Docking Results? Original score CSV and docking pose SDF files will be retained.`,
      )
    )
      return;
    try {
      await api.deleteDockingResultLigands(project.id, [...unique.values()]);
      const removed = new Set(unique.keys());
      const remaining = results.filter(
        (row) => !removed.has(`${row.job_id}:${row.ligand_code}`),
      );
      setSelectedKeys(new Set());
      setVisibleKeys(
        new Set(
          [...visibleKeys].filter((key) =>
            remaining.some((row) => row.key === key),
          ),
        ),
      );
      setActivePose(remaining[0]);
      refresh();
    } catch (error) {
      onNotice(error instanceof Error ? error.message : String(error));
    }
  };
  const loadSummary = async (file: File) => {
    try {
      const parsed = parseDelimited(await file.text());
      if (parsed.length < 2)
        throw new Error("Summary CSV contains no data rows");
      const rawHeaders = parsed[0].map((header) => header.trim()),
        headers = rawHeaders.map(normalizeColumn),
        column = (...names: string[]) =>
          headers.findIndex((header) => names.includes(header));
      const codeIndex = column("ligandcode", "ligand", "code"),
        poseIndex = column("pose", "mode"),
        rankIndex = column("rank"),
        jobIndex = column("jobid"),
        affinityIndex = column("affinity");
      if (codeIndex < 0 || poseIndex < 0)
        throw new Error("Summary CSV requires ligand_code and pose columns");
      const filenameJob = file.name.match(/^(.+)-scores\.csv$/i)?.[1],
        rowJob = jobIndex >= 0 ? parsed[1]?.[jobIndex] : undefined,
        targetJob = results.some(
          (row) => row.job_id === (rowJob || filenameJob),
        )
          ? rowJob || filenameJob
          : new Set(results.map((row) => row.job_id)).size === 1
            ? results[0]?.job_id
            : undefined;
      let matched = 0;
      const updates = new Map<string, string[]>();
      parsed.slice(1).forEach((values) => {
        const code = values[codeIndex],
          pose = Number(values[poseIndex]);
        if (!code || !Number.isFinite(pose)) return;
        results
          .filter(
            (row) =>
              row.ligand_code === code &&
              row.pose === pose &&
              (!targetJob || row.job_id === targetJob),
          )
          .forEach((row) => updates.set(row.key, values));
      });
      const updated = results.map((row) => {
        const values = updates.get(row.key);
        if (!values) return row;
        matched++;
        const scores = { ...(row.scores || {}) };
        headers.forEach((header, index) => {
          if (
            [
              "rank",
              "ligandcode",
              "ligand",
              "code",
              "ligandid",
              "pose",
              "mode",
              "jobid",
            ].includes(header)
          )
            return;
          const value = Number(values[index]);
          if (Number.isFinite(value))
            scores[rawHeaders[index] || header] = value;
        });
        const affinity =
            affinityIndex >= 0 ? Number(values[affinityIndex]) : NaN,
          rank = rankIndex >= 0 ? Number(values[rankIndex]) : NaN;
        return {
          ...row,
          rank: Number.isFinite(rank) ? rank : row.rank,
          affinity: Number.isFinite(affinity) ? affinity : row.affinity,
          scores,
        };
      });
      if (!matched)
        throw new Error(
          "No rows in this summary CSV match the current docking results",
        );
      setResults(updated);
      setSummaryMessage(`${file.name}: ${matched} rows loaded`);
    } catch (error) {
      onNotice(error instanceof Error ? error.message : String(error));
    } finally {
      if (summaryInput.current) summaryInput.current.value = "";
    }
  };
  const scoreCell = (row: DockingResult, key: string) =>
    format3(key === "affinity" ? row.affinity : row.scores?.[key]);
  if (!results.length)
    return (
      <div className="p-4">
        <div className="panel-head">
          <span>Docking results</span>
          <div className="ml-auto flex gap-2">
            <input
              ref={summaryInput}
              type="file"
              accept=".csv,text/csv"
              className="hidden"
              onChange={(event) =>
                event.target.files?.[0] && loadSummary(event.target.files[0])
              }
            />
            <button
              className="btn-secondary"
              onClick={() => summaryInput.current?.click()}
            >
              <Upload />
              Load summary CSV
            </button>
            <button className="btn-secondary" onClick={refresh}>
              <RefreshCw />
              Refresh
            </button>
          </div>
        </div>
        <div className="empty-panel min-h-36">
          <Database className="mx-auto mb-2 h-7 w-7 text-slate-700" />
          <div>No completed docking results yet.</div>
          <div className="mt-1 text-xs text-slate-600">
            Run docking and refresh after the Job Monitor reports COMPLETED. A
            summary CSV can update matching result rows after their pose files
            are available.
          </div>
        </div>
      </div>
    );
  return (
    <div className="p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[.18em] text-slate-500">
            Docking results
          </div>
          <div className="text-xs text-slate-600">
            Checkboxes select rows; eye buttons pin poses in the Viewer
            independently.
          </div>
          {summaryMessage && (
            <div className="mt-1 text-[10px] text-mint">{summaryMessage}</div>
          )}
        </div>
        <div className="ml-auto flex gap-2">
          <input
            ref={summaryInput}
            type="file"
            accept=".csv,text/csv"
            className="hidden"
            onChange={(event) =>
              event.target.files?.[0] && loadSummary(event.target.files[0])
            }
          />
          <button
            className="btn-secondary"
            onClick={() => summaryInput.current?.click()}
          >
            <Upload />
            Load summary CSV
          </button>
          <button
            className="btn-secondary text-red-300"
            disabled={!selectedKeys.size}
            title="Hide selected ligands without deleting score CSV or pose SDF files"
            onClick={deleteSelectedLigands}
          >
            <Trash2 />
            Delete selected ligands
          </button>
          <button className="btn-secondary" onClick={refresh}>
            <RefreshCw />
            Refresh
          </button>
          <button
            className="btn-secondary"
            disabled={!selectedKeys.size}
            title="Export checked docking-result rows only"
            onClick={exportCsv}
          >
            <Download />
            Export selected CSV
          </button>
          <button
            className="btn-secondary"
            disabled={!selectedKeys.size}
            title="Export checked poses as one score-annotated SDF"
            onClick={exportSdf}
          >
            <Download />
            Export selected SDF
          </button>
          <button
            className="btn-secondary"
            disabled={!selectedKeys.size || !tools.pymol?.available}
            title={
              tools.pymol?.available
                ? "Export selected poses and receptor as PyMOL session"
                : tools.pymol?.error || "PyMOL is unavailable"
            }
            onClick={exportPse}
          >
            Export selected .pse
          </button>
        </div>
      </div>
      <div className="table-wrap max-h-80 overflow-auto">
        <table>
          <thead>
            <tr>
              <th></th>
              <th
                onClick={() => setSortKey("rank")}
                className="cursor-pointer select-none"
              >
                Rank{indicator("rank")}
              </th>
              <th
                onClick={() => setSortKey("receptor_name")}
                className="cursor-pointer select-none"
              >
                Receptor{indicator("receptor_name")}
              </th>
              <th
                onClick={() => setSortKey("ligand_name")}
                className="cursor-pointer select-none"
              >
                Ligand{indicator("ligand_name")}
              </th>
              <th
                onClick={() => setSortKey("pose")}
                className="cursor-pointer select-none"
              >
                Pose{indicator("pose")}
              </th>
              <th
                onClick={() => setSortKey("affinity")}
                className="cursor-pointer select-none"
              >
                Affinity{indicator("affinity")}
              </th>
              {scoreColumns.map((key) => (
                <th
                  key={key}
                  onClick={() => setSortKey("score:" + key)}
                  className="cursor-pointer select-none"
                >
                  {scoreLabel(key)}
                  {indicator("score:" + key)}
                </th>
              ))}
              <th
                onClick={() => setSortKey("engine")}
                className="cursor-pointer select-none"
              >
                Engine{indicator("engine")}
              </th>
              <th
                onClick={() => setSortKey("job_name")}
                className="cursor-pointer select-none"
              >
                Job{indicator("job_name")}
              </th>
            </tr>
          </thead>
          <tbody>
            {sortedResults.map((row) => (
              <tr
                key={row.key}
                title="Click to activate this pose; use ↑/↓ to move the active row"
                tabIndex={activePose?.key === row.key ? 0 : -1}
                aria-selected={activePose?.key === row.key}
                className={
                  "cursor-pointer " +
                  (activePose?.key === row.key ? "bg-cyan/10" : "")
                }
                onClick={() => setActivePose(row)}
                onKeyDown={(event) => {
                  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                    event.preventDefault();
                    moveKeyboard(event.key === "ArrowDown" ? 1 : -1);
                  }
                }}
              >
                <td>
                  <div className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      aria-label={
                        "Select " + row.ligand_code + " pose " + row.pose
                      }
                      checked={selectedKeys.has(row.key)}
                      onChange={() => toggle(row)}
                      onClick={(event) => event.stopPropagation()}
                    />
                    <button
                      type="button"
                      className={`icon-btn h-7 w-7 ${visibleKeys.has(row.key) ? "text-cyan" : ""}`}
                      aria-label={
                        (visibleKeys.has(row.key) ? "Hide " : "Show ") +
                        row.ligand_code +
                        " pose " +
                        row.pose +
                        " in viewer"
                      }
                      title={
                        visibleKeys.has(row.key)
                          ? "Hide pinned pose from Viewer"
                          : "Pin pose in Viewer"
                      }
                      onClick={(event) => {
                        event.stopPropagation();
                        toggleVisible(row);
                      }}
                    >
                      {visibleKeys.has(row.key) ? <Eye /> : <EyeOff />}
                    </button>
                    <button
                      type="button"
                      className="icon-btn h-7 w-7"
                      aria-label={
                        "Focus " +
                        row.ligand_code +
                        " pose " +
                        row.pose +
                        " in viewer"
                      }
                      title="Focus pose and residues within 5 Å"
                      onClick={(event) => {
                        event.stopPropagation();
                        focusRow(row);
                      }}
                    >
                      <Focus />
                    </button>
                  </div>
                </td>
                <td className="font-mono">{row.rank}</td>
                <td className="text-xs text-slate-400">
                  {row.receptor_name || "—"}
                </td>
                <td>
                  <span className="font-semibold">{row.ligand_name}</span>
                  <span className="ml-2 text-xs text-slate-500">
                    {row.ligand_code}
                  </span>
                </td>
                <td className="font-mono">{row.pose}</td>
                <td className="font-mono">{scoreCell(row, "affinity")}</td>
                {scoreColumns.map((key) => (
                  <td key={key} className="font-mono">
                    {scoreCell(row, key)}
                  </td>
                ))}
                <td>{row.engine || "—"}</td>
                <td className="text-xs text-slate-500">{row.job_name}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-2 text-[10px] text-slate-600">
        列見出しクリックでソート。チェックボックスは削除・CSV/SDF/PSE出力用の選択、目のボタンはViewerへの固定表示、フォーカスボタンはposeと周辺5
        Åへのフォーカスです。行のactive状態や↑/↓移動ではチェック状態を変更しません。Load
        summary
        CSVは現在のposeファイルに一致するスコア行だけを読み込み、元CSVとSDFは変更しません。
      </div>
    </div>
  );
}
