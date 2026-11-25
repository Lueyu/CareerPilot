type Address = { pathname: string; search?: string; hash?: string };

const identifier = /^[1-9]\d*$/;

export function interviewLocation({ pathname, search = "", hash = "" }: Address): string {
  const parts = pathname.split("/").filter(Boolean);
  const [root, section, id] = parts;
  const params = new URLSearchParams(search);
  function destination(path: string) {
    const query = params.toString();
    return path + (query ? "?" + query : "") + hash;
  }
  function context(key: string, value: string | null | undefined) {
    if (value && !params.has(key)) params.set(key, value);
  }
  function chat(id?: string | null) {
    return destination(id && identifier.test(id) ? "/chat/" + id : "/chat");
  }

  if (root === "chat" || root === "library" || root === "competency" || root === "settings") {
    return pathname + search + hash;
  }
  if (root === "study" || root === "coach") {
    if (section === "chat") return chat(id);
    if (section === "notes") {
      context("note", id);
      return destination("/library/materials");
    }
    if (section === "import") {
      context("import", "1");
      return destination(params.get("kind") === "jd" ? "/competency" : "/library/notes");
    }
    if (section === "library") return destination("/library");
    if (section === "profile" || section === "role") return destination("/competency");
    if (section === "history") return destination("/library/history");
    if (section === "new") {
      context("new", "1");
      return chat();
    }
    if (section === "retest" || section === "train") {
      const session = params.get("session");
      if (params.get("mode") === "study" && session && identifier.test(session))
        return chat(session);
      const attempt = params.get("id");
      if (attempt && identifier.test(attempt)) {
        return destination(
          "/chat/exam/" +
            attempt +
            (id === "report" || params.get("mode") === "review" ? "/report" : ""),
        );
      }
      if (section === "retest") context("skill", "review");
      return section === "train" ? destination("/chat/exam") : chat();
    }
    if (section === "plan") context("skill", "prep");
    if (section === "commands") context("palette", "1");
    return chat();
  }
  if (root === "bank")
    return destination(params.get("review") === "1" ? "/library/notes" : "/library/questions");
  if (root === "benchmark" || root === "profile") {
    context("dimension", section);
    return destination("/competency");
  }
  if (root === "podcasts") return destination("/library/podcasts");
  if (root === "assets") {
    if (section === "pipeline") return destination("/library/notes");
    context("project", id);
    return destination("/library/projects");
  }
  if (root === "exam") {
    const attempt = params.get("id") ?? (section && identifier.test(section) ? section : null);
    if (attempt && identifier.test(attempt)) {
      return destination("/chat/exam/" + attempt + (id === "report" ? "/report" : ""));
    }
    return destination("/chat/exam");
  }
  if (root === "learning") {
    context("skill", section === "task" ? "review" : "prep");
    if (section === "task") context("task", id);
    return chat();
  }
  if (root === "memory") {
    context("memory", "1");
    return chat();
  }
  if (root === "onboarding") return destination("/competency");
  if (["ops", "observatory", "evolution"].includes(root)) return destination("/settings");
  return destination("/chat");
}
