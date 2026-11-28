import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, within, waitFor } from "@testing-library/react";
import CourseInstructorBox, {
  filterPeople,
  personName,
  type CourseInstructorRow,
  type StaffCandidate,
} from "./CourseInstructorBox";
import CourseDetailPage from "@/app/admin/courses/[id]/page";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "474" }),
  useRouter: () => ({ push: vi.fn() }),
}));

// Synthetic accounts only.
const TEACHERS: StaffCandidate[] = [
  { id: 11, username: "t_parvathy", first_name: "Kalamandalam", last_name: "Parvathy Panicker", phone_number: "+910000000011", is_active: true },
  { id: 12, username: "t_rema", first_name: "Rema", last_name: "Devi", email: "rema@example.com", is_active: true },
  { id: 13, username: "t_old", first_name: "Anil", last_name: "Kumar", phone_number: "+910000000013", is_active: false },
  { id: 14, username: "t_both", first_name: "Bindu", last_name: "Both", phone_number: "+910000000014", is_active: true },
];
const MENTORS: StaffCandidate[] = [
  { id: 21, username: "m_arya", first_name: "ARYA", last_name: "MENTOR", phone_number: "+910000000021", is_active: true },
  { id: 22, username: "m_devika", first_name: "Devika", last_name: "S", email: "devika@example.com", is_active: true },
  { id: 14, username: "t_both", first_name: "Bindu", last_name: "Both", phone_number: "+910000000014", is_active: true },
];
const ROWS: CourseInstructorRow[] = [
  { id: 101, user: 11, user_name: "Kalamandalam Parvathy Panicker", user_phone: "+910000000011", is_active_user: true, role: "TEACHER", is_primary: true },
  { id: 102, user: 21, user_name: "ARYA MENTOR", user_phone: "+910000000021", is_active_user: true, role: "MENTOR" },
  { id: 103, user: 12, user_name: "Rema Devi", user_email: "rema@example.com", is_active_user: true, role: "ASSISTANT" },
];

const names = (people: StaffCandidate[]) => people.map(personName);

describe("filterPeople", () => {
  it("lists active people A–Z first, then inactive ones", () => {
    expect(names(filterPeople(TEACHERS, ""))).toEqual([
      "Bindu Both",
      "Kalamandalam Parvathy Panicker",
      "Rema Devi",
      "Anil Kumar",
    ]);
  });

  it("matches name words in any order, username, email and phone digits", () => {
    expect(names(filterPeople(TEACHERS, "panicker parvathy"))).toEqual(["Kalamandalam Parvathy Panicker"]);
    expect(names(filterPeople(TEACHERS, "t_rema"))).toEqual(["Rema Devi"]);
    expect(names(filterPeople(TEACHERS, "rema@example"))).toEqual(["Rema Devi"]);
    expect(names(filterPeople(TEACHERS, "0000000013"))).toEqual(["Anil Kumar"]);
    expect(filterPeople(TEACHERS, "nobody")).toEqual([]);
  });

  it("falls back to the username when a person has no name", () => {
    expect(personName({ id: 1, username: "only_user", first_name: "", last_name: null })).toBe("only_user");
  });
});

describe("CourseInstructorBox", () => {
  const props = (group: "TEACHER" | "MENTOR", overrides = {}) => ({
    group,
    instructors: ROWS,
    loading: false,
    canManage: true,
    candidates: group === "TEACHER" ? TEACHERS : MENTORS,
    candidatesStatus: "ready" as const,
    onLoadCandidates: vi.fn(),
    onAdd: vi.fn(async () => null),
    onRemove: vi.fn(),
    removingId: null,
    ...overrides,
  });

  const optionNames = () =>
    within(screen.getByRole("listbox")).getAllByRole("option").map((o) => o.querySelector(".font-semibold")?.textContent);

  it("Teachers box lists only teacher and assistant rows", () => {
    render(<CourseInstructorBox {...props("TEACHER")} />);
    expect(screen.getByRole("heading", { name: "Teachers" })).toBeInTheDocument();
    expect(screen.getByText("Kalamandalam Parvathy Panicker")).toBeInTheDocument();
    expect(screen.getByText("Rema Devi")).toBeInTheDocument();
    expect(screen.getByText("ASSISTANT")).toBeInTheDocument();
    expect(screen.queryByText("ARYA MENTOR")).not.toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("Mentors box lists only mentor rows", () => {
    render(<CourseInstructorBox {...props("MENTOR")} />);
    expect(screen.getByRole("heading", { name: "Mentors" })).toBeInTheDocument();
    expect(screen.getByText("ARYA MENTOR")).toBeInTheDocument();
    expect(screen.queryByText("Kalamandalam Parvathy Panicker")).not.toBeInTheDocument();
    expect(screen.queryByText("Rema Devi")).not.toBeInTheDocument();
    expect(screen.getByText("1")).toBeInTheDocument();
  });

  it("shows an empty state, and hides add/remove for non-admins", () => {
    render(<CourseInstructorBox {...props("MENTOR", { instructors: [ROWS[0]], canManage: false })} />);
    expect(screen.getByText("No mentor assigned yet.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Add Mentor/ })).not.toBeInTheDocument();
  });

  it("loads candidates when the add panel first opens, and offers a retry on error", () => {
    const p = props("TEACHER", { candidates: [], candidatesStatus: "idle" });
    const { rerender } = render(<CourseInstructorBox {...p} />);
    fireEvent.click(screen.getByRole("button", { name: "+ Add Teacher" }));
    expect(p.onLoadCandidates).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("combobox", { name: "Search teachers" })).toHaveFocus();

    rerender(<CourseInstructorBox {...p} candidatesStatus="loading" />);
    expect(screen.getByText("Loading teachers…")).toBeInTheDocument();

    rerender(<CourseInstructorBox {...p} candidatesStatus="error" />);
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(p.onLoadCandidates).toHaveBeenCalledTimes(2);
  });

  it("does not reload candidates that are already loaded", () => {
    const p = props("MENTOR");
    render(<CourseInstructorBox {...p} />);
    fireEvent.click(screen.getByRole("button", { name: "+ Add Mentor" }));
    expect(p.onLoadCandidates).not.toHaveBeenCalled();
    expect(screen.getByText("3 mentors")).toBeInTheDocument();
  });

  it("Teachers search shows teachers only and filters as you type", () => {
    render(<CourseInstructorBox {...props("TEACHER")} />);
    fireEvent.click(screen.getByRole("button", { name: "+ Add Teacher" }));
    expect(optionNames()).toEqual(names(filterPeople(TEACHERS, "")));
    expect(screen.queryByText("Devika S")).not.toBeInTheDocument();

    const search = screen.getByRole("combobox", { name: "Search teachers" });
    fireEvent.change(search, { target: { value: "devika" } });
    expect(screen.getByText("No teacher matches “devika”.")).toBeInTheDocument();

    fireEvent.change(search, { target: { value: "rema" } });
    expect(optionNames()).toEqual(["Rema Devi"]);
    expect(screen.getByText("Showing 1 of 4 teachers")).toBeInTheDocument();
  });

  it("Mentors search shows mentors only", () => {
    render(<CourseInstructorBox {...props("MENTOR")} />);
    fireEvent.click(screen.getByRole("button", { name: "+ Add Mentor" }));
    expect(optionNames()).toEqual(["ARYA MENTOR", "Bindu Both", "Devika S"]);
    fireEvent.change(screen.getByRole("combobox", { name: "Search mentors" }), { target: { value: "parvathy" } });
    expect(screen.getByText("No mentor matches “parvathy”.")).toBeInTheDocument();
  });

  it("disables people who already hold the role being added", () => {
    const p = props("TEACHER");
    render(<CourseInstructorBox {...p} />);
    fireEvent.click(screen.getByRole("button", { name: "+ Add Teacher" }));

    const parvathy = screen.getByRole("option", { name: /Kalamandalam Parvathy Panicker/ });
    expect(parvathy).toHaveAttribute("aria-disabled", "true");
    expect(within(parvathy).getByText(/Already teacher/)).toBeInTheDocument();
    fireEvent.click(parvathy);
    expect(parvathy).toHaveAttribute("aria-selected", "false");
    expect(screen.getByRole("button", { name: "Add Teacher" })).toBeDisabled();

    // Rema is an assistant, not a teacher: she can be added as a teacher, not as an assistant.
    const rema = () => screen.getByRole("option", { name: /Rema Devi/ });
    fireEvent.click(rema());
    expect(rema()).toHaveAttribute("aria-selected", "true");
    fireEvent.click(screen.getByRole("button", { name: "Assistant" }));
    expect(rema()).toHaveAttribute("aria-disabled", "true");
    expect(rema()).toHaveAttribute("aria-selected", "false");
    expect(screen.getByRole("button", { name: "Add Assistant" })).toBeDisabled();
  });

  it("adds the picked teacher with the chosen role and primary flag, then closes", async () => {
    const p = props("TEACHER");
    render(<CourseInstructorBox {...p} />);
    fireEvent.click(screen.getByRole("button", { name: "+ Add Teacher" }));
    fireEvent.click(screen.getByRole("option", { name: /Bindu Both/ }));
    expect(screen.getByText("Selected:")).toHaveTextContent("Selected: Bindu Both");
    fireEvent.click(screen.getByRole("checkbox", { name: "Set as primary instructor" }));
    fireEvent.click(screen.getByRole("button", { name: "Add Teacher" }));

    expect(p.onAdd).toHaveBeenCalledWith(14, "TEACHER", true);
    await waitFor(() => expect(screen.queryByRole("combobox")).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: "+ Add Teacher" })).toBeInTheDocument();
  });

  it("adds a mentor with the MENTOR role", async () => {
    const p = props("MENTOR");
    render(<CourseInstructorBox {...p} />);
    fireEvent.click(screen.getByRole("button", { name: "+ Add Mentor" }));
    expect(screen.queryByRole("button", { name: "Assistant" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("option", { name: /Devika S/ }));
    fireEvent.click(screen.getByRole("button", { name: "Add Mentor" }));
    expect(p.onAdd).toHaveBeenCalledWith(22, "MENTOR", false);
    await waitFor(() => expect(screen.queryByRole("combobox")).not.toBeInTheDocument());
  });

  it("keeps the panel open and shows the server's message when adding fails", async () => {
    const p = props("MENTOR", { onAdd: vi.fn(async () => "This user already has this role on this course.") });
    render(<CourseInstructorBox {...p} />);
    fireEvent.click(screen.getByRole("button", { name: "+ Add Mentor" }));
    fireEvent.click(screen.getByRole("option", { name: /Devika S/ }));
    fireEvent.click(screen.getByRole("button", { name: "Add Mentor" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This user already has this role on this course.");
    expect(screen.getByRole("combobox")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add Mentor" })).toBeEnabled();
  });

  it("supports arrow keys and Enter to pick (never to add), and Escape to close", () => {
    const p = props("MENTOR");
    render(<CourseInstructorBox {...p} />);
    fireEvent.click(screen.getByRole("button", { name: "+ Add Mentor" }));
    const search = screen.getByRole("combobox");
    fireEvent.keyDown(search, { key: "ArrowDown" });
    fireEvent.keyDown(search, { key: "ArrowDown" });
    fireEvent.keyDown(search, { key: "ArrowDown" }); // stays on the last one
    fireEvent.keyDown(search, { key: "ArrowUp" });
    expect(search).toHaveAttribute("aria-activedescendant", "mentor-candidates-14");
    fireEvent.keyDown(search, { key: "Enter" });
    expect(screen.getByRole("option", { name: /Bindu Both/ })).toHaveAttribute("aria-selected", "true");
    expect(p.onAdd).not.toHaveBeenCalled();

    fireEvent.keyDown(search, { key: "Escape" });
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("removes through the row's delete button", () => {
    const p = props("TEACHER");
    render(<CourseInstructorBox {...p} />);
    fireEvent.click(screen.getByRole("button", { name: "Remove Rema Devi" }));
    expect(p.onRemove).toHaveBeenCalledWith(ROWS[2]);
  });
});

describe("Admin course page: Teachers and Mentors boxes", () => {
  let instructors: CourseInstructorRow[];
  let fetchMock: ReturnType<typeof vi.fn>;
  // Teachers come back over two pages to check that every page is loaded.
  const teacherPage = (page: number) =>
    page === 1
      ? { count: 4, next: "http://x/?page=2", results: TEACHERS.slice(0, 2).map((t) => ({ ...t, is_teacher: true })) }
      : { count: 4, next: null, results: TEACHERS.slice(2).map((t) => ({ ...t, is_teacher: true })) };

  const json = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

  beforeEach(() => {
    instructors = [ROWS[0], ROWS[1]];
    fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
      const url = new URL(input);
      const path = url.pathname;
      if (path === "/api/auth/user/") return json({ id: 1, username: "admin", is_superuser: true, is_staff: true });
      if (path === "/api/courses/474/") {
        return json({ id: 474, title: "Mohiniyattam", description: "", price: "0", course_type: "LIVE", is_published: true, modules: [], thumbnail: null });
      }
      if (path === "/api/courses/474/instructors/") {
        if (init?.method === "POST") {
          const body = JSON.parse(String(init.body));
          const person = [...TEACHERS, ...MENTORS].find((u) => u.id === body.user)!;
          instructors = [...instructors, { id: 200 + instructors.length, user: body.user, user_name: personName(person), role: body.role, is_active_user: true, is_primary: body.is_primary }];
          return json({}, 201);
        }
        return json(instructors);
      }
      if (path === "/api/courses/474/students/") return json({ students: [], count: 0, batches: [] });
      if (path === "/api/users/admin-users/") {
        const role = url.searchParams.get("role");
        if (role === "teachers") return json(teacherPage(Number(url.searchParams.get("page"))));
        if (role === "mentors") return json({ count: 3, next: null, results: MENTORS.map((m) => ({ ...m, is_mentor: true })) });
      }
      return json([]);
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  const box = (group: "teacher" | "mentor") => screen.getByTestId(`instructor-box-${group}`);
  const staffCalls = (role: string) =>
    fetchMock.mock.calls.filter(([u]) => String(u).includes("/api/users/admin-users/") && String(u).includes(`role=${role}`));

  it("shows teachers and mentors in separate boxes, and loads no staff list until asked", async () => {
    render(<CourseDetailPage />);
    await waitFor(() => expect(within(box("teacher")).getByText("Kalamandalam Parvathy Panicker")).toBeInTheDocument());
    expect(within(box("mentor")).getByText("ARYA MENTOR")).toBeInTheDocument();
    expect(within(box("teacher")).queryByText("ARYA MENTOR")).not.toBeInTheDocument();
    expect(within(box("mentor")).queryByText("Kalamandalam Parvathy Panicker")).not.toBeInTheDocument();
    expect(screen.queryByText("Assigned Instructors")).not.toBeInTheDocument();
    expect(staffCalls("teachers")).toHaveLength(0);
    expect(staffCalls("mentors")).toHaveLength(0);
  });

  it("Teachers box searches every teacher page and adds as TEACHER", async () => {
    render(<CourseDetailPage />);
    const teachers = await waitFor(() => {
      const b = box("teacher");
      expect(within(b).getByText("Kalamandalam Parvathy Panicker")).toBeInTheDocument();
      return b;
    });
    fireEvent.click(within(teachers).getByRole("button", { name: "+ Add Teacher" }));
    expect(await within(teachers).findByText("4 teachers")).toBeInTheDocument();
    expect(staffCalls("teachers").map(([u]) => new URL(String(u)).searchParams.get("page"))).toEqual(["1", "2"]);
    expect(staffCalls("mentors")).toHaveLength(0);
    expect(within(teachers).queryByText("Devika S")).not.toBeInTheDocument();

    fireEvent.change(within(teachers).getByRole("combobox"), { target: { value: "anil" } });
    fireEvent.click(within(teachers).getByRole("option", { name: /Anil Kumar/ }));
    fireEvent.click(within(teachers).getByRole("button", { name: "Add Teacher" }));

    await waitFor(() => expect(within(box("teacher")).getByText("Anil Kumar")).toBeInTheDocument());
    const post = fetchMock.mock.calls.find(([u, i]) => String(u).endsWith("/api/courses/474/instructors/") && i?.method === "POST")!;
    expect(JSON.parse(String(post[1].body))).toEqual({ user: 13, role: "TEACHER", is_primary: false });
    expect(within(box("mentor")).queryByText("Anil Kumar")).not.toBeInTheDocument();
  });

  it("Mentors box searches mentors only and adds as MENTOR", async () => {
    render(<CourseDetailPage />);
    await waitFor(() => expect(within(box("mentor")).getByText("ARYA MENTOR")).toBeInTheDocument());
    const mentors = box("mentor");
    fireEvent.click(within(mentors).getByRole("button", { name: "+ Add Mentor" }));
    expect(await within(mentors).findByText("3 mentors")).toBeInTheDocument();
    expect(staffCalls("teachers")).toHaveLength(0);
    expect(within(mentors).queryByText("Rema Devi")).not.toBeInTheDocument();

    fireEvent.click(within(mentors).getByRole("option", { name: /Devika S/ }));
    fireEvent.click(within(mentors).getByRole("button", { name: "Add Mentor" }));
    await waitFor(() => expect(within(box("mentor")).getByText("Devika S")).toBeInTheDocument());
    const post = fetchMock.mock.calls.find(([u, i]) => String(u).endsWith("/api/courses/474/instructors/") && i?.method === "POST")!;
    expect(JSON.parse(String(post[1].body))).toEqual({ user: 22, role: "MENTOR", is_primary: false });
    expect(within(box("teacher")).queryByText("Devika S")).not.toBeInTheDocument();
  });

  it("shows a retry when the staff list fails to load", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    let fail = true;
    const base = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (input: string, init?: RequestInit) =>
      fail && String(input).includes("role=mentors") ? json({ detail: "boom" }, 500) : base(input, init),
    );
    render(<CourseDetailPage />);
    await waitFor(() => expect(within(box("mentor")).getByText("ARYA MENTOR")).toBeInTheDocument());
    fireEvent.click(within(box("mentor")).getByRole("button", { name: "+ Add Mentor" }));
    expect(await within(box("mentor")).findByText("Couldn't load the mentor list.")).toBeInTheDocument();
    fail = false;
    fireEvent.click(within(box("mentor")).getByRole("button", { name: "Try again" }));
    expect(await within(box("mentor")).findByText("3 mentors")).toBeInTheDocument();
  });
});
