import { useCallback, useEffect, useState } from "react";
import { api, type BankData } from "../../api/client";

type Inventory = Pick<BankData, "total" | "items"> & {
  facets?: { topics?: { name: string; count: number }[] };
};
type ReadState =
  | { kind: "loading"; path: string }
  | { kind: "ready"; path: string; data: Inventory }
  | { kind: "error"; path: string; error: string };
const inventoryPageSize = 200;

export async function loadBankInventory(path: string, signal: AbortSignal): Promise<Inventory> {
  const [pathname, search] = path.split("?");
  const params = new URLSearchParams(search);
  params.set("limit", String(inventoryPageSize));
  const items: Inventory["items"] = [];
  const ids = new Set<number>();
  let total: number | undefined;
  let facets: Inventory["facets"];
  do {
    signal.throwIfAborted();
    params.set("offset", String(items.length));
    const page = await api.get<Inventory>(pathname + "?" + params, signal);
    signal.throwIfAborted();
    if (
      !Number.isSafeInteger(page.total) ||
      page.total < 0 ||
      (total !== undefined && total !== page.total) ||
      page.items.length > inventoryPageSize ||
      items.length + page.items.length > page.total ||
      (page.items.length === 0 && items.length < page.total)
    ) {
      throw new Error("题库内容已变化或分页不完整，请重试。");
    }
    total = page.total;
    facets ??= page.facets;
    for (const item of page.items) {
      if (ids.has(item.id)) throw new Error("题库分页存在重复，请重试。");
      ids.add(item.id);
      items.push(item);
    }
  } while (items.length < total);
  return { total, items, facets };
}

export function useBankInventory(path: string) {
  const [state, setState] = useState<ReadState>({ kind: "loading", path });
  const [revision, setRevision] = useState(0);
  const reload = useCallback(() => setRevision((value) => value + 1), []);
  useEffect(() => {
    const controller = new AbortController();
    setState({ kind: "loading", path });
    void loadBankInventory(path, controller.signal).then(
      (data) => {
        if (!controller.signal.aborted) setState({ kind: "ready", path, data });
      },
      (error) => {
        if (!controller.signal.aborted)
          setState({
            kind: "error",
            path,
            error: error instanceof Error ? error.message : "请求失败，请重试。",
          });
      },
    );
    return () => controller.abort();
  }, [path, revision]);
  const current: ReadState = state.path === path ? state : { kind: "loading", path };
  return {
    data: current.kind === "ready" ? current.data : undefined,
    error: current.kind === "error" ? current.error : undefined,
    loading: current.kind === "loading",
    reload,
  };
}
