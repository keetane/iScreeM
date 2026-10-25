const jsonHeaders = { "Content-Type": "application/json" };
async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const data = await response.json();
      message = data.detail || message;
    } catch {}
    throw new Error(message);
  }
  return response.json();
}
export const api = {
  tools: () =>
    request<Record<string, import("./types").Tool>>("/api/system/tools"),
  projects: () => request<{ id: string; name: string }[]>("/api/projects"),
  project: (id: string) =>
    request<import("./types").Project>(`/api/projects/${id}`),
  createProject: (name: string) =>
    request<{ id: string; name: string }>("/api/projects", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ name }),
    }),
  uploadProtein: (id: string, file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request(`/api/projects/${id}/protein`, { method: "POST", body });
  },
  deleteProtein: (projectId: string, proteinId: string) =>
    request(`/api/projects/${projectId}/proteins/${proteinId}`, {
      method: "DELETE",
    }),
  predictPockets: (projectId: string, proteinId: string) =>
    request(
      `/api/projects/${projectId}/pockets/predict?protein_id=${proteinId}`,
      { method: "POST" },
    ),
  prepareReceptor: (projectId: string, proteinId: string) =>
    request(
      `/api/projects/${projectId}/receptor/prepare?protein_id=${proteinId}`,
      { method: "POST" },
    ),
  createGrid: (projectId: string, pocketId: string, name: string) =>
    request(`/api/projects/${projectId}/grids`, {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ pocket_id: pocketId, name }),
    }),
  patchGrid: (id: string, body: unknown) =>
    request(`/api/grids/${id}`, {
      method: "PATCH",
      headers: jsonHeaders,
      body: JSON.stringify(body),
    }),
  deleteGrid: (id: string) => request(`/api/grids/${id}`, { method: "DELETE" }),
  deletePocket: (projectId: string, pocketId: string) =>
    request(`/api/projects/${projectId}/pockets/${pocketId}`, {
      method: "DELETE",
    }),
  importLigands: (
    projectId: string,
    ligands: { name?: string; smiles: string }[],
  ) =>
    request(`/api/projects/${projectId}/ligands`, {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ ligands }),
    }),
  patchLigand: (
    projectId: string,
    ligandId: string,
    body: { ligand_code?: string; name?: string; smiles?: string },
  ) =>
    request(`/api/projects/${projectId}/ligands/${ligandId}`, {
      method: "PATCH",
      headers: jsonHeaders,
      body: JSON.stringify(body),
    }),
  deleteLigands: (projectId: string, ligandIds: string[]) =>
    request(`/api/projects/${projectId}/ligands/delete`, {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ ligand_ids: ligandIds }),
    }),
  deleteLigand: (projectId: string, ligandId: string) =>
    request(`/api/projects/${projectId}/ligands/${ligandId}`, {
      method: "DELETE",
    }),
  prepareLigands: (projectId: string, ligandIds: string[]) =>
    request(`/api/projects/${projectId}/ligands/prepare`, {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ ligand_ids: ligandIds }),
    }),
  runDocking: (projectId: string, body: unknown) =>
    request(`/api/projects/${projectId}/docking-jobs`, {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify(body),
    }),
  uploadDockingLigands: (projectId: string, file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request<import("./types").DockingUpload>(
      `/api/projects/${projectId}/docking-ligands/upload`,
      { method: "POST", body },
    );
  },
  dockingResults: (projectId: string) =>
    request<import("./types").DockingResult[]>(
      `/api/projects/${projectId}/docking-results`,
    ),
  deleteDockingResultLigands: (
    projectId: string,
    ligands: { job_id: string; ligand_code: string }[],
  ) =>
    request(`/api/projects/${projectId}/docking-results/delete-ligands`, {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ ligands }),
    }),
  dockingScoresUrl: (projectId: string, jobId: string) =>
    `/api/projects/${projectId}/docking-results/${jobId}/scores.csv`,
  exportDockingCsv: async (
    projectId: string,
    poses: { job_id: string; ligand_code: string; pose: number }[],
  ) => {
    const response = await fetch(
      `/api/projects/${projectId}/docking-results/export-csv`,
      { method: "POST", headers: jsonHeaders, body: JSON.stringify({ poses }) },
    );
    if (!response.ok) {
      let message = `Request failed (${response.status})`;
      try {
        const data = await response.json();
        message = data.detail || message;
      } catch {}
      throw new Error(message);
    }
    const disposition = response.headers.get("Content-Disposition") || "";
    const filename =
      disposition.match(/filename="?([^";]+)"?/i)?.[1] ||
      "selected-docking-results.csv";
    return { blob: await response.blob(), filename };
  },
  exportDockingSdf: async (
    projectId: string,
    poses: { job_id: string; ligand_code: string; pose: number }[],
  ) => {
    const response = await fetch(
      `/api/projects/${projectId}/docking-results/export-sdf`,
      { method: "POST", headers: jsonHeaders, body: JSON.stringify({ poses }) },
    );
    if (!response.ok) {
      let message = `Request failed (${response.status})`;
      try {
        const data = await response.json();
        message = data.detail || message;
      } catch {}
      throw new Error(message);
    }
    const disposition = response.headers.get("Content-Disposition") || "";
    const filename =
      disposition.match(/filename="?([^";]+)"?/i)?.[1] ||
      "selected-docking-poses.sdf";
    return { blob: await response.blob(), filename };
  },
  exportPse: (projectId: string, body: unknown) =>
    request<{ download_url: string; filename: string }>(
      `/api/projects/${projectId}/docking-results/export-pse`,
      { method: "POST", headers: jsonHeaders, body: JSON.stringify(body) },
    ),
  jobs: () => request<import("./types").Job[]>("/api/jobs"),
  cancelJob: (id: string) =>
    request(`/api/jobs/${id}/cancel`, { method: "POST" }),
};
