import { useEffect, useRef, useState } from "react";
import {
  Camera,
  Focus,
  LocateFixed,
  RotateCcw,
  Layers3,
  Hexagon,
} from "lucide-react";
import type { Grid, Pocket, Protein, StructureLigand } from "../types";

type Props = {
  protein?: Protein;
  grid?: Grid;
  pockets: Pocket[];
  visiblePockets: Set<string>;
  visibleStructureLigands: Set<string>;
  dockingPoses?: { key: string; url: string; pose: number }[];
  dockingPoseFocus?: { key: string; nonce: number };
  focusRequest?:
    | { kind: "grid"; grid: Grid; nonce: number }
    | { kind: "ligand"; ligand: StructureLigand; nonce: number };
  selectedResidues: Set<string>;
  onSelectResidue: (key: string) => void;
  onClearResidues: () => void;
};
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
const ligandStickStyle = {
  colorscheme: "Jmol",
  radius: 0.22,
  singleBonds: false,
  doubleBondScaling: 0.4,
  tripleBondScaling: 0.25,
  aromaticStyle: "dashed",
};
const waterResidues = { resn: ["HOH", "WAT", "DOD"] };
const interactionWaterResidues = new Set(["HOH", "WAT", "DOD"]);
// PDBFixer/PDB2PQR writes covalently modified amino acids (for example the
// phosphohistidine NEP in 6ETJ) as HETATM. Treat these as part of the protein
// representation instead of leaving a visible gap or showing them as ligand.
const modifiedProteinResidues = {
  resn: ["MSE", "NEP", "SEP", "TPO", "PTR", "HYP", "CSO", "CSD", "CME", "MLY"],
};
const interactionElement = (atom: any) => String(atom.elem || "").toUpperCase();
const isHydrogenBondDonor = (atom: any) =>
  ["N", "O", "S"].includes(interactionElement(atom)) &&
  Number(atom.charge || 0) <= 1;
const isHydrogenBondAcceptor = (atom: any) =>
  ["N", "O", "S"].includes(interactionElement(atom)) &&
  Number(atom.charge || 0) <= 0;
const residueSelector = (key: string) => {
  const [chain, ...number] = key.split(":");
  const value = number.join(":");
  return { chain, resi: /^\d+$/.test(value) ? Number(value) : value };
};
const pocketResidueSelector = (value: string) => {
  const match = value.match(/^(.+?)_(.+)$/);
  return match ? residueSelector(`${match[1]}:${match[2]}`) : undefined;
};
const structureLigandKey = (ligand: StructureLigand) =>
  `${ligand.chain || "*"}:${ligand.residue_number || "*"}:${ligand.name}`;
const structureLigandSelector = (ligand: StructureLigand) => ({
  ...{ hetflag: true, resn: ligand.name },
  ...(ligand.chain ? { chain: ligand.chain } : {}),
  ...(ligand.residue_number
    ? {
        resi: /^\d+$/.test(ligand.residue_number)
          ? Number(ligand.residue_number)
          : ligand.residue_number,
      }
    : {}),
});

export function Viewer({
  protein,
  grid,
  pockets,
  visiblePockets,
  visibleStructureLigands,
  dockingPoses = [],
  dockingPoseFocus,
  focusRequest,
  selectedResidues,
  onSelectResidue,
  onClearResidues,
}: Props) {
  const host = useRef<HTMLDivElement>(null),
    viewer = useRef<any>(null),
    model = useRef<any>(null),
    poseModels = useRef<Map<string, any>>(new Map()),
    appliedDockingFocusNonce = useRef<number>(),
    surfaceType = useRef<any>(null),
    surfaceGeneration = useRef(0),
    focusNeighborhood = useRef<any>(),
    onSelectResidueRef = useRef(onSelectResidue),
    atomClickRef = useRef(false),
    ctrlLeftClickRef = useRef(false);
  const [showWater, setShowWater] = useState(false),
    [showHetero, setShowHetero] = useState(true),
    [showInteractions, setShowInteractions] = useState(true),
    [showSurface, setShowSurface] = useState(false);
  const [viewerReady, setViewerReady] = useState(false),
    [modelVersion, setModelVersion] = useState(0),
    [poseVersion, setPoseVersion] = useState(0),
    [hasHetero, setHasHetero] = useState(false);

  useEffect(() => {
    onSelectResidueRef.current = onSelectResidue;
  }, [onSelectResidue]);
  useEffect(() => {
    let alive = true;
    import("3dmol").then(($3Dmol) => {
      if (!host.current || !alive) return;
      surfaceType.current = $3Dmol.SurfaceType.SAS;
      viewer.current = $3Dmol.createViewer(host.current, {
        backgroundColor: "#07111f",
        antialias: true,
      });
      viewer.current.render();
      setViewerReady(true);
    });
    return () => {
      alive = false;
      surfaceGeneration.current += 1;
    };
  }, []);
  useEffect(() => {
    if (!viewerReady || !viewer.current) return;
    const canvas = viewer.current.getCanvas() as HTMLCanvasElement;
    let clipGesture:
      | { x: number; y: number; near: number; far: number }
      | undefined;
    const mouseDown = (event: MouseEvent) => {
      ctrlLeftClickRef.current = event.button === 0 && event.ctrlKey;
      if (event.button === 2 && event.shiftKey) {
        const slab = viewer.current.getSlab();
        clipGesture = {
          x: event.clientX,
          y: event.clientY,
          near: slab.near,
          far: slab.far,
        };
      }
    };
    const mouseMove = (event: MouseEvent) => {
      if (!clipGesture || !viewer.current) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      const width = Math.max(1, canvas.clientWidth),
        height = Math.max(1, canvas.clientHeight),
        near =
          clipGesture.near + ((event.clientY - clipGesture.y) / height) * 100,
        far = clipGesture.far - ((event.clientX - clipGesture.x) / width) * 100;
      if (far - near > 1) viewer.current.setSlab(near, far);
      viewer.current.render();
    };
    const mouseUp = () => {
      clipGesture = undefined;
      window.setTimeout(() => {
        ctrlLeftClickRef.current = false;
      }, 0);
    };
    const wheel = (event: WheelEvent) => {
      if (!viewer.current) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      const slab = viewer.current.getSlab(),
        amount =
          Math.sign(event.deltaY) *
          Math.min(3, Math.max(0.25, Math.abs(event.deltaY) * 0.02)),
        near = slab.near - amount,
        far = slab.far + amount;
      if (far - near > 1) viewer.current.setSlab(near, far);
      viewer.current.render();
    };
    const contextMenu = (event: MouseEvent) => {
      if (event.ctrlKey) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    };
    canvas.addEventListener("mousedown", mouseDown, true);
    canvas.addEventListener("mousemove", mouseMove, true);
    canvas.addEventListener("wheel", wheel, { capture: true, passive: false });
    canvas.addEventListener("contextmenu", contextMenu, true);
    window.addEventListener("mouseup", mouseUp, true);
    return () => {
      canvas.removeEventListener("mousedown", mouseDown, true);
      canvas.removeEventListener("mousemove", mouseMove, true);
      canvas.removeEventListener("wheel", wheel, true);
      canvas.removeEventListener("contextmenu", contextMenu, true);
      window.removeEventListener("mouseup", mouseUp, true);
    };
  }, [viewerReady]);
  useEffect(() => {
    if (!viewerReady || !viewer.current || !protein) return;
    let cancelled = false;
    focusNeighborhood.current = undefined;
    viewer.current.removeAllModels();
    poseModels.current.clear();
    model.current = null;
    setHasHetero(false);
    viewer.current.render();
    const sourceFormat =
      protein.stats.source_format || protein.stats.format || "pdb";
    fetch(`/api/proteins/${protein.id}/file`)
      .then((r) => {
        if (!r.ok) throw new Error(`Structure download failed (${r.status})`);
        return r.text();
      })
      .then((data) => {
        if (cancelled || !viewer.current) return;
        model.current = viewer.current.addModel(
          data,
          sourceFormat === "cif" ? "cif" : "pdb",
        );
        const centerAtomResidue = (atom: any) => {
          const selection: any = { resi: atom.resi, resn: atom.resn };
          if (atom.chain) selection.chain = atom.chain;
          viewer.current.center(selection, 300);
          viewer.current.render();
        };
        const handleProteinClick = (
          atom: any,
          _viewer: any,
          event?: MouseEvent,
        ) => {
          atomClickRef.current = true;
          if (
            (event?.ctrlKey || ctrlLeftClickRef.current) &&
            atom?.resi !== undefined
          ) {
            centerAtomResidue(atom);
            return;
          }
          if (atom?.chain !== undefined && atom?.resi !== undefined)
            onSelectResidueRef.current(`${atom.chain || "_"}:${atom.resi}`);
        };
        const handleHeteroClick = (
          atom: any,
          _viewer: any,
          event?: MouseEvent,
        ) => {
          atomClickRef.current = true;
          if (
            (event?.ctrlKey || ctrlLeftClickRef.current) &&
            atom?.resi !== undefined &&
            !interactionWaterResidues.has(String(atom.resn || "").toUpperCase())
          )
            centerAtomResidue(atom);
        };
        model.current.setClickable(
          { hetflag: false },
          true,
          handleProteinClick,
        );
        model.current.setClickable({ hetflag: true }, true, handleHeteroClick);
        model.current.setClickable(
          modifiedProteinResidues,
          true,
          handleProteinClick,
        );
        const heteroAtoms = model.current
          .selectedAtoms({ hetflag: true })
          .filter(
            (atom: any) =>
              !interactionWaterResidues.has(
                String(atom.resn || "").toUpperCase(),
              ),
          );
        setHasHetero(heteroAtoms.length > 0);
        applyStyle();
        viewer.current.zoomTo();
        viewer.current.render();
        setModelVersion((version) => version + 1);
      })
      .catch((error) => console.error(error));
    return () => {
      cancelled = true;
    };
  }, [
    viewerReady,
    protein?.id,
    protein?.stats.format,
    protein?.stats.source_format,
  ]);

  useEffect(() => {
    if (!viewerReady || !viewer.current) return;
    let cancelled = false;
    const requested = new Map(dockingPoses.map((pose) => [pose.key, pose]));
    let removed = false;
    poseModels.current.forEach((poseModel, key) => {
      if (!requested.has(key)) {
        viewer.current.removeModel(poseModel);
        poseModels.current.delete(key);
        removed = true;
      }
    });
    if (!dockingPoses.length) {
      viewer.current.render();
      if (removed) setPoseVersion((version) => version + 1);
      return;
    }
    const focusPose = () => {
      if (
        !dockingPoseFocus ||
        appliedDockingFocusNonce.current === dockingPoseFocus.nonce ||
        !viewer.current
      )
        return;
      const poseModel = poseModels.current.get(dockingPoseFocus.key);
      if (!poseModel) return;
      const id = poseModel.getID?.();
      if (id === undefined) return;
      const poseSelection = { model: id };
      viewer.current.zoomTo(
        {
          or: [poseSelection, { within: { distance: 5, sel: poseSelection } }],
        },
        400,
      );
      appliedDockingFocusNonce.current = dockingPoseFocus.nonce;
      viewer.current.render();
    };
    let added = false;
    Promise.all(
      dockingPoses.map(async (pose) => {
        if (cancelled || poseModels.current.has(pose.key)) return;
        const response = await fetch(pose.url);
        if (!response.ok)
          throw new Error(`Pose download failed (${response.status})`);
        const data = await response.text();
        if (cancelled || !viewer.current) return;
        const poseModel = viewer.current.addModel(data, "sdf");
        poseModel.setStyle(
          {},
          { stick: { ...ligandStickStyle, radius: 0.28 } },
        );
        poseModels.current.set(pose.key, poseModel);
        added = true;
      }),
    )
      .then(() => {
        if (!cancelled) {
          viewer.current?.render();
          if (added || removed) setPoseVersion((version) => version + 1);
          focusPose();
        }
      })
      .catch((error) => {
        if (!cancelled) console.error(error);
      });
    return () => {
      cancelled = true;
    };
  }, [viewerReady, dockingPoses, modelVersion]);

  useEffect(() => {
    if (!viewerReady || !viewer.current || !model.current || !focusRequest)
      return;
    let neighborhood: any, zoomSelection: any;
    if (focusRequest.kind === "grid") {
      const { center, size } = focusRequest.grid,
        padding = 10,
        bounds = {
          minX: center.x - size.x / 2 - padding,
          maxX: center.x + size.x / 2 + padding,
          minY: center.y - size.y / 2 - padding,
          maxY: center.y + size.y / 2 + padding,
          minZ: center.z - size.z / 2 - padding,
          maxZ: center.z + size.z / 2 + padding,
        };
      neighborhood = {
        byres: true,
        predicate: (atom: any) =>
          !atom.hetflag &&
          atom.x >= bounds.minX &&
          atom.x <= bounds.maxX &&
          atom.y >= bounds.minY &&
          atom.y <= bounds.maxY &&
          atom.z >= bounds.minZ &&
          atom.z <= bounds.maxZ,
      };
      zoomSelection = neighborhood;
    } else {
      const ligandSelection = structureLigandSelector(focusRequest.ligand);
      neighborhood = {
        hetflag: false,
        byres: true,
        within: { distance: 10, sel: ligandSelection },
      };
      zoomSelection = { or: [ligandSelection, neighborhood] };
    }
    focusNeighborhood.current = neighborhood;
    applyStyle();
    if (model.current.selectedAtoms(neighborhood).length)
      viewer.current.zoomTo(zoomSelection, 400);
    else if (focusRequest.kind === "grid") {
      const currentView = viewer.current.getView(),
        center = focusRequest.grid.center;
      viewer.current.setView([
        -center.x,
        -center.y,
        -center.z,
        ...currentView.slice(3),
      ]);
    }
    viewer.current.render();
  }, [viewerReady, focusRequest]);
  useEffect(() => {
    if (
      !viewerReady ||
      !viewer.current ||
      !dockingPoseFocus ||
      appliedDockingFocusNonce.current === dockingPoseFocus.nonce
    )
      return;
    const poseModel = poseModels.current.get(dockingPoseFocus.key);
    if (!poseModel) return;
    const id = poseModel.getID?.();
    if (id === undefined) return;
    const poseSelection = { model: id };
    viewer.current.zoomTo(
      { or: [poseSelection, { within: { distance: 5, sel: poseSelection } }] },
      400,
    );
    appliedDockingFocusNonce.current = dockingPoseFocus.nonce;
    viewer.current.render();
  }, [viewerReady, dockingPoseFocus, modelVersion]);

  const visibleLigandAtoms = () =>
    protein && showHetero
      ? (protein.stats.hetero_molecules || [])
          .filter((ligand) =>
            visibleStructureLigands.has(structureLigandKey(ligand)),
          )
          .flatMap(
            (ligand) =>
              model.current?.selectedAtoms(structureLigandSelector(ligand)) ||
              [],
          )
      : [];
  const visibleDockingPoseAtoms = () =>
    [...poseModels.current.values()].flatMap(
      (poseModel) => poseModel.selectedAtoms?.({}) || [],
    );
  const applyStyle = () => {
    if (!viewer.current || !model.current) return;
    const generation = ++surfaceGeneration.current;
    const proteinSelection = { hetflag: false };
    const surfaceColorProperty = "__iscreem_surface_color";
    model.current.setStyle({}, {});
    viewer.current.removeAllSurfaces?.();
    const activePockets = pockets.filter((p) => visiblePockets.has(p.id));
    model.current.setStyle(proteinSelection, { cartoon: { color: "#8ccbd4" } });
    model.current.setStyle(modifiedProteinResidues, {
      cartoon: { color: "#8ccbd4" },
    });
    activePockets.forEach((p) => {
      const color = pocketColorForRank(p.rank);
      p.residues
        .map(pocketResidueSelector)
        .filter(Boolean)
        .forEach((selection) =>
          model.current.setStyle(selection, {
            cartoon: { color },
            line: { color, linewidth: 1 },
          }),
        );
    });
    if (showHetero && protein)
      (protein.stats.hetero_molecules || [])
        .filter((ligand) =>
          visibleStructureLigands.has(structureLigandKey(ligand)),
        )
        .forEach((ligand) =>
          model.current.setStyle(structureLigandSelector(ligand), {
            stick: ligandStickStyle,
          }),
        );
    model.current.setStyle(modifiedProteinResidues, {
      cartoon: { color: "#8ccbd4" },
    });
    selectedResidues.forEach((key) =>
      model.current.setStyle(residueSelector(key), {
        cartoon: { color: "#ffffff" },
        line: { color: "#ffffff", linewidth: 1 },
      }),
    );
    if (focusNeighborhood.current)
      model.current.addStyle?.(focusNeighborhood.current, {
        line: { color: "#f3b84b", linewidth: 2 },
      });
    model.current.setStyle(
      waterResidues,
      showWater ? { sphere: { colorscheme: "Jmol", scale: 0.35 } } : {},
    );
    if (showSurface && surfaceType.current !== undefined) {
      const surfaceColors: Record<string, string> = {
        base: "#5a9fb2",
        selected: "#ffffff",
      };
      model.current.selectedAtoms(proteinSelection).forEach((atom: any) => {
        atom.properties = atom.properties || {};
        atom.properties[surfaceColorProperty] = "base";
      });
      model.current
        .selectedAtoms(modifiedProteinResidues)
        .forEach((atom: any) => {
          atom.properties = atom.properties || {};
          atom.properties[surfaceColorProperty] = "base";
        });
      activePockets.forEach((p) => {
        const colorKey = `pocket${p.rank}`;
        surfaceColors[colorKey] = pocketColorForRank(p.rank);
        p.residues
          .map(pocketResidueSelector)
          .filter(Boolean)
          .forEach((selection) =>
            model.current.selectedAtoms(selection).forEach((atom: any) => {
              atom.properties = atom.properties || {};
              atom.properties[surfaceColorProperty] = colorKey;
            }),
          );
      });
      selectedResidues.forEach((key) =>
        model.current
          .selectedAtoms(residueSelector(key))
          .forEach((atom: any) => {
            atom.properties = atom.properties || {};
            atom.properties[surfaceColorProperty] = "selected";
          }),
      );
      const surface = viewer.current.addSurface(
        surfaceType.current,
        {
          colorscheme: { prop: surfaceColorProperty, map: surfaceColors },
          opacity: 0.8,
        },
        proteinSelection,
      );
      if (surface?.then) {
        surface
          .then(() => {
            if (generation !== surfaceGeneration.current) {
              viewer.current?.removeSurface(surface.surfid);
            } else viewer.current?.render();
          })
          .catch((error: any) => {
            if (generation === surfaceGeneration.current)
              console.error("Surface generation failed", error);
          });
      }
    }
    viewer.current.render();
  };
  useEffect(applyStyle, [
    showSurface,
    showWater,
    showHetero,
    pockets,
    visiblePockets,
    visibleStructureLigands,
    selectedResidues,
  ]);
  useEffect(() => {
    const v = viewer.current;
    if (!v) return;
    v.removeAllShapes();
    pockets
      .filter((p) => visiblePockets.has(p.id))
      .forEach((p) => {
        const color = pocketColorForRank(p.rank);
        if (p.points?.length)
          p.points.slice(0, 2500).forEach((point) =>
            v.addSphere({
              center: { x: point[0], y: point[1], z: point[2] },
              radius: 0.35,
              color,
              opacity: 0.7,
            }),
          );
        else
          v.addSphere({ center: p.center, radius: 2.2, color, opacity: 0.24 });
      });
    if (showInteractions && model.current) {
      const ligandAtoms = [
        ...visibleLigandAtoms(),
        ...visibleDockingPoseAtoms(),
      ].filter(
        (atom: any) =>
          interactionElement(atom) !== "H" &&
          (isHydrogenBondDonor(atom) || isHydrogenBondAcceptor(atom)),
      );
      const receptorAtoms = model.current
        .selectedAtoms({ hetflag: false })
        .filter(
          (atom: any) =>
            interactionElement(atom) !== "H" &&
            (isHydrogenBondDonor(atom) || isHydrogenBondAcceptor(atom)),
        );
      const contacts: any[] = [];
      ligandAtoms.forEach((ligand: any) => {
        let nearest: any;
        let nearestDistance = Infinity;
        receptorAtoms.forEach((receptor: any) => {
          const dx = ligand.x - receptor.x,
            dy = ligand.y - receptor.y,
            dz = ligand.z - receptor.z,
            distance = dx * dx + dy * dy + dz * dz;
          const hydrogenBond =
            (isHydrogenBondDonor(ligand) && isHydrogenBondAcceptor(receptor)) ||
            (isHydrogenBondAcceptor(ligand) && isHydrogenBondDonor(receptor));
          if (hydrogenBond && distance <= 12.96 && distance < nearestDistance) {
            nearest = receptor;
            nearestDistance = distance;
          }
        });
        if (nearest) contacts.push({ ligand, receptor: nearest });
      });
      contacts.slice(0, 200).forEach(({ ligand, receptor }) => {
        const start = { x: ligand.x, y: ligand.y, z: ligand.z },
          end = { x: receptor.x, y: receptor.y, z: receptor.z };
        v.addLine({ start, end, color: "#ffd34d", linewidth: 4 });
        v.addCylinder({ start, end, color: "#ffd34d", radius: 0.08 });
      });
    }
    if (grid) {
      const c = grid.center,
        s = grid.size,
        gridPocket = pockets.find((p) => p.id === grid.pocket_id),
        gridColor = gridPocket
          ? pocketColorForRank(gridPocket.rank)
          : "#47d7e8";
      const min = { x: c.x - s.x / 2, y: c.y - s.y / 2, z: c.z - s.z / 2 },
        max = { x: c.x + s.x / 2, y: c.y + s.y / 2, z: c.z + s.z / 2 };
      const corners = [
        { x: min.x, y: min.y, z: min.z },
        { x: max.x, y: min.y, z: min.z },
        { x: max.x, y: max.y, z: min.z },
        { x: min.x, y: max.y, z: min.z },
        { x: min.x, y: min.y, z: max.z },
        { x: max.x, y: min.y, z: max.z },
        { x: max.x, y: max.y, z: max.z },
        { x: min.x, y: max.y, z: max.z },
      ];
      v.addBox({
        center: c,
        dimensions: { w: s.x, h: s.y, d: s.z },
        color: gridColor,
        opacity: 0.08,
      });
      const edges = [
        [0, 1],
        [1, 2],
        [2, 3],
        [3, 0],
        [4, 5],
        [5, 6],
        [6, 7],
        [7, 4],
        [0, 4],
        [1, 5],
        [2, 6],
        [3, 7],
      ];
      edges.forEach(([a, b]) =>
        v.addLine({
          start: corners[a],
          end: corners[b],
          color: gridColor,
          linewidth: 2,
        }),
      );
    }
    v.render();
  }, [
    grid,
    pockets,
    visiblePockets,
    visibleStructureLigands,
    showHetero,
    showInteractions,
    modelVersion,
    poseVersion,
  ]);
  const download = () => {
    if (!viewer.current) return;
    const now = new Date(),
      pad = (value: number) => String(value).padStart(2, "0"),
      timestamp = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}`,
      receptor =
        (protein?.name || "receptor")
          .trim()
          .replace(/[^\p{L}\p{N}_.-]+/gu, "_")
          .replace(/^[_\-.]+|[_\-.]+$/g, "") || "receptor";
    const link = document.createElement("a");
    link.href = viewer.current.pngURI();
    link.download = `${timestamp}_${receptor}.png`;
    link.click();
  };
  const focusSelected = () => {
    if (!viewer.current) return;
    const selections = [...selectedResidues].map(residueSelector);
    const selection =
      selections.length === 1
        ? selections[0]
        : selections.length
          ? { or: selections }
          : {};
    viewer.current.center(selection, 400);
    viewer.current.render();
  };
  const focusLargestHetero = () => {
    if (!viewer.current || !model.current) return;
    const atoms = model.current
      .selectedAtoms({ hetflag: true })
      .filter(
        (atom: any) =>
          !interactionWaterResidues.has(String(atom.resn || "").toUpperCase()),
      );
    const groups = new Map<string, { atom: any; count: number }>();
    atoms.forEach((atom: any) => {
      const key = `${atom.chain || "_"}:${atom.resi}:${atom.resn || ""}`;
      const group = groups.get(key);
      if (group) group.count += 1;
      else groups.set(key, { atom, count: 1 });
    });
    const largest = [...groups.values()].sort((a, b) => b.count - a.count)[0];
    if (!largest) return;
    const selection = {
      chain: largest.atom.chain || "_",
      resi: largest.atom.resi,
    };
    viewer.current.zoomTo(selection, 400);
    viewer.current.render();
  };
  const handleViewerClick = () => {
    if (atomClickRef.current) {
      atomClickRef.current = false;
      return;
    }
    window.setTimeout(() => {
      if (!atomClickRef.current) onClearResidues();
      atomClickRef.current = false;
    }, 0);
  };
  return (
    <div className="relative h-full min-h-0 overflow-hidden rounded-2xl border border-line bg-ink shadow-2xl shadow-black/30">
      <div
        ref={host}
        onClickCapture={handleViewerClick}
        className="absolute inset-0"
        aria-label="3D molecular viewer"
      />
      {!protein && (
        <div className="absolute inset-0 grid place-items-center pointer-events-none">
          <div className="text-center">
            <Layers3 className="mx-auto mb-4 h-10 w-10 text-cyan/40" />
            <p className="text-sm text-slate-400">
              Upload a PDB structure to begin
            </p>
            <p className="mt-1 text-xs text-slate-600">
              Coordinates remain unchanged during viewing
            </p>
          </div>
        </div>
      )}
      <div className="absolute right-3 top-3 flex gap-1 rounded-lg border border-line bg-panel/90 p-1 backdrop-blur">
        <button
          className="icon-btn"
          title="Reset view"
          onClick={() => {
            viewer.current?.zoomTo();
            viewer.current?.render();
          }}
        >
          <RotateCcw />
        </button>
        <button
          className="icon-btn"
          title="Fit protein"
          onClick={() => {
            viewer.current?.zoomTo({});
            viewer.current?.render();
          }}
        >
          <Focus />
        </button>
        <button
          className="icon-btn"
          title={
            selectedResidues.size
              ? "Center selected residue(s)"
              : "Select a residue first"
          }
          disabled={!selectedResidues.size}
          onClick={focusSelected}
        >
          <LocateFixed />
        </button>
        <button
          className="icon-btn"
          title={hasHetero ? "Focus largest HETATM" : "No ligand HETATM found"}
          disabled={!hasHetero}
          onClick={focusLargestHetero}
        >
          <Hexagon />
        </button>
        <button className="icon-btn" title="Save PNG" onClick={download}>
          <Camera />
        </button>
      </div>
      <div className="absolute bottom-3 left-3 flex gap-4 rounded-lg border border-line bg-panel/90 px-3 py-2 text-xs text-slate-300 backdrop-blur">
        <label>
          <input
            type="checkbox"
            checked={showHetero}
            onChange={(e) => setShowHetero(e.target.checked)}
          />{" "}
          Hetero
        </label>
        <label>
          <input
            type="checkbox"
            checked={showWater}
            onChange={(e) => setShowWater(e.target.checked)}
          />{" "}
          Water
        </label>
        <label>
          <input
            type="checkbox"
            checked={showInteractions}
            onChange={(e) => setShowInteractions(e.target.checked)}
          />{" "}
          Interactions
        </label>
        <label>
          <input
            type="checkbox"
            checked={showSurface}
            onChange={(e) => setShowSurface(e.target.checked)}
          />{" "}
          Surface
        </label>
      </div>
      <div className="pointer-events-none absolute bottom-3 right-3 rounded-lg border border-line bg-panel/90 px-3 py-2 text-[10px] text-slate-500 backdrop-blur">
        PyMOL mouse · L rotate · Ctrl+L set center · M move · R zoom · Wheel
        slab · Shift+R clip
      </div>
    </div>
  );
}
