import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, within, waitFor } from "@testing-library/react";
import AssignCourseModal, { filterCourses, type CatalogCourse } from "./AssignCourseModal";
import UserDetailPage from "@/app/admin/users/[id]/page";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "42" }),
  useRouter: () => ({ push: vi.fn() }),
}));

const CATALOG: CatalogCourse[] = [
  { id: 1, title: "CARNATIC MUSIC ENGLISH UNIT 2", course_type: "LIVE", is_published: true },
  { id: 2, title: "KUCHIPUDI MALAYALM UNIT2", course_type: "LIVE", is_published: true },
  { id: 3, title: "Bharathanatyam Malayalam Unit 1", course_type: "RECORDED", is_published: true },
  { id: 4, title: "KUCHIPUDI ENGLISH UNIT 2", course_type: "LIVE", is_published: true },
  { id: 5, title: "DHYANA DEVI (CM DIPLOMA)", course_type: "RECORDED", is_published: false },
  { id: 6, title: "Bharathanatyam Malayalam Unit 10", course_type: "RECORDED", is_published: true },
  { id: 7, title: "മോഹിനിയാട്ടം", course_type: "RECORDED", is_published: true },
];

const titles = (courses: CatalogCourse[]) => courses.map((c) => c.title);

describe("filterCourses", () => {
  it("returns every course A–Z with natural number order when the search is empty", () => {
    expect(titles(filterCourses(CATALOG, ""))).toEqual([
      "Bharathanatyam Malayalam Unit 1",
      "Bharathanatyam Malayalam Unit 10",
      "CARNATIC MUSIC ENGLISH UNIT 2",
      "DHYANA DEVI (CM DIPLOMA)",
      "KUCHIPUDI ENGLISH UNIT 2",
      "KUCHIPUDI MALAYALM UNIT2",
      "മോഹിനിയാട്ടം",
    ]);
  });

  it("matches every typed word, in any order, ignoring case", () => {
    expect(titles(filterCourses(CATALOG, "unit 2 kuchipudi"))).toEqual([
      "KUCHIPUDI ENGLISH UNIT 2",
      "KUCHIPUDI MALAYALM UNIT2",
    ]);
    expect(titles(filterCourses(CATALOG, "  English   KUCHIPUDI "))).toEqual(["KUCHIPUDI ENGLISH UNIT 2"]);
  });

  it("ignores spacing and punctuation", () => {
    expect(titles(filterCourses(CATALOG, "unit2 english"))).toEqual([
      "CARNATIC MUSIC ENGLISH UNIT 2",
      "KUCHIPUDI ENGLISH UNIT 2",
    ]);
    expect(titles(filterCourses(CATALOG, "cm diploma"))).toEqual(["DHYANA DEVI (CM DIPLOMA)"]);
    expect(titles(filterCourses(CATALOG, "(dhyana)"))).toEqual(["DHYANA DEVI (CM DIPLOMA)"]);
  });

  it("finds titles written in Malayalam script", () => {
    expect(titles(filterCourses(CATALOG, "മോഹിനി"))).toEqual(["മോഹിനിയാട്ടം"]);
  });

  it("limits results to one course type", () => {
    expect(titles(filterCourses(CATALOG, "", "LIVE"))).toEqual([
      "CARNATIC MUSIC ENGLISH UNIT 2",
      "KUCHIPUDI ENGLISH UNIT 2",
      "KUCHIPUDI MALAYALM UNIT2",
    ]);
    expect(titles(filterCourses(CATALOG, "malayalam", "RECORDED"))).toEqual([
      "Bharathanatyam Malayalam Unit 1",
      "Bharathanatyam Malayalam Unit 10",
    ]);
  });

  it("returns nothing when no title matches, and does not reorder the caller's array", () => {
    const input = [...CATALOG];
    expect(filterCourses(input, "kathakali")).toEqual([]);
    filterCourses(input, "");
    expect(input).toEqual(CATALOG);
  });
});

describe("AssignCourseModal", () => {
  const baseProps = () => ({
    userName: "Test Student",
    courses: CATALOG,
    status: "ready" as const,
    onRetry: vi.fn(),
    assigned: [{ course_id: 2, role: "Enrolled" }],
    assigning: false,
    onAssign: vi.fn(),
    onClose: vi.fn(),
  });

  const optionTitles = () =>
    within(screen.getByRole("listbox")).getAllByRole("option").map((o) => o.querySelector("[title]")?.getAttribute("title"));

  it("shows the whole catalog sorted, focuses the search box and names the user", () => {
    render(<AssignCourseModal {...baseProps()} />);
    expect(screen.getByRole("combobox", { name: "Search courses" })).toHaveFocus();
    expect(screen.getByText("Test Student")).toBeInTheDocument();
    expect(screen.getByText("7 courses")).toBeInTheDocument();
    expect(optionTitles()).toEqual(titles(filterCourses(CATALOG, "")));
  });

  it("filters as the admin types, with a clear button and an empty state", () => {
    render(<AssignCourseModal {...baseProps()} />);
    const search = screen.getByRole("combobox", { name: "Search courses" });

    fireEvent.change(search, { target: { value: "kuchipudi english" } });
    expect(optionTitles()).toEqual(["KUCHIPUDI ENGLISH UNIT 2"]);
    expect(screen.getByText("Showing 1 of 7 courses")).toBeInTheDocument();

    fireEvent.change(search, { target: { value: "kathakali" } });
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(screen.getByText(/No courses match/)).toHaveTextContent("No courses match “kathakali”.");

    fireEvent.click(screen.getByRole("button", { name: "Clear search and filters" }));
    expect(search).toHaveValue("");
    expect(optionTitles()).toHaveLength(7);

    fireEvent.change(search, { target: { value: "dhyana" } });
    fireEvent.click(screen.getByRole("button", { name: "Clear search" }));
    expect(search).toHaveValue("");
    expect(optionTitles()).toHaveLength(7);
  });

  it("filters by course type and shows per-type counts for the current search", () => {
    render(<AssignCourseModal {...baseProps()} />);
    const liveTab = screen.getByRole("button", { name: /^Live/ });
    expect(liveTab).toHaveTextContent("Live3");
    expect(screen.getByRole("button", { name: /^Recorded/ })).toHaveTextContent("Recorded4");

    fireEvent.click(liveTab);
    expect(liveTab).toHaveAttribute("aria-pressed", "true");
    expect(optionTitles()).toEqual([
      "CARNATIC MUSIC ENGLISH UNIT 2",
      "KUCHIPUDI ENGLISH UNIT 2",
      "KUCHIPUDI MALAYALM UNIT2",
    ]);

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "carnatic" } });
    expect(screen.getByRole("button", { name: /^All/ })).toHaveTextContent("All1");
    expect(screen.getByRole("button", { name: /^Recorded/ })).toHaveTextContent("Recorded0");
  });

  it("marks draft and already-assigned courses", () => {
    render(<AssignCourseModal {...baseProps()} />);
    const draft = screen.getByRole("option", { name: /DHYANA DEVI/ });
    expect(within(draft).getByText("Draft")).toBeInTheDocument();
    const assigned = screen.getByRole("option", { name: /KUCHIPUDI MALAYALM UNIT2/ });
    expect(within(assigned).getByText(/Already assigned · Enrolled/)).toBeInTheDocument();
    expect(screen.getAllByText(/Already assigned ·/)).toHaveLength(1);
  });

  it("assigns only after a course is picked, and sends that course's id", () => {
    const props = baseProps();
    render(<AssignCourseModal {...props} />);
    const assignButton = screen.getByRole("button", { name: "Assign Access" });
    expect(assignButton).toBeDisabled();
    expect(screen.getByText("No course selected")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("option", { name: /KUCHIPUDI ENGLISH UNIT 2/ }));
    expect(screen.getByRole("option", { name: /KUCHIPUDI ENGLISH UNIT 2/ })).toHaveAttribute("aria-selected", "true");
    expect(assignButton).toBeEnabled();

    fireEvent.click(assignButton);
    expect(props.onAssign).toHaveBeenCalledTimes(1);
    expect(props.onAssign).toHaveBeenCalledWith(4);
  });

  it("keeps the selection when the search changes, and explains re-assigning an assigned course", () => {
    const props = baseProps();
    render(<AssignCourseModal {...props} />);
    fireEvent.click(screen.getByRole("option", { name: /KUCHIPUDI MALAYALM UNIT2/ }));
    expect(screen.getByText(/Already assigned \(Enrolled\)\. Assigning again won.t create a duplicate\./)).toBeInTheDocument();

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "carnatic" } });
    fireEvent.click(screen.getByRole("button", { name: "Assign Access" }));
    expect(props.onAssign).toHaveBeenCalledWith(2);
  });

  it("supports arrow keys and Enter to pick, without assigning on Enter", () => {
    const props = baseProps();
    render(<AssignCourseModal {...props} />);
    const search = screen.getByRole("combobox");
    fireEvent.change(search, { target: { value: "kuchipudi" } });

    fireEvent.keyDown(search, { key: "ArrowDown" });
    expect(search).toHaveAttribute("aria-activedescendant", "assign-course-option-2");
    fireEvent.keyDown(search, { key: "ArrowDown" }); // stays on the last result
    fireEvent.keyDown(search, { key: "ArrowUp" });
    expect(search).toHaveAttribute("aria-activedescendant", "assign-course-option-4");
    fireEvent.keyDown(search, { key: "Enter" });

    expect(screen.getByRole("option", { name: /KUCHIPUDI ENGLISH UNIT 2/ })).toHaveAttribute("aria-selected", "true");
    expect(props.onAssign).not.toHaveBeenCalled();
  });

  it("closes on Escape, the close button, Cancel and the backdrop, but not on clicks inside", () => {
    const props = baseProps();
    const { container } = render(<AssignCourseModal {...props} />);
    fireEvent.click(screen.getByRole("dialog"));
    expect(props.onClose).not.toHaveBeenCalled();

    fireEvent.keyDown(window, { key: "Escape" });
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(container.firstChild as HTMLElement);
    expect(props.onClose).toHaveBeenCalledTimes(4);
  });

  it("cannot be closed or re-submitted while assigning", () => {
    const props = { ...baseProps(), assigning: true };
    const { container } = render(<AssignCourseModal {...props} />);
    fireEvent.keyDown(window, { key: "Escape" });
    fireEvent.click(container.firstChild as HTMLElement);
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Close" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Assigning/ })).toBeDisabled();
    expect(props.onClose).not.toHaveBeenCalled();
  });

  it("shows loading, and an error with a working retry", () => {
    const props = baseProps();
    const { rerender } = render(<AssignCourseModal {...props} courses={[]} status="loading" />);
    expect(screen.getByText("Loading courses…")).toBeInTheDocument();
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();

    rerender(<AssignCourseModal {...props} courses={[]} status="error" />);
    expect(screen.getByText("Couldn't load the course list.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(props.onRetry).toHaveBeenCalledTimes(1);

    rerender(<AssignCourseModal {...props} courses={[]} status="ready" />);
    expect(screen.getByText("No courses in the catalog yet.")).toBeInTheDocument();
  });
});

describe("Admin user page: Assign Course", () => {
  const API = "http://localhost:8000";
  let catalogOk: boolean;
  let assignedCourses: { course_id: number; title: string; role: string }[];
  let fetchMock: ReturnType<typeof vi.fn>;
  let alertMock: ReturnType<typeof vi.fn>;

  const json = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

  beforeEach(() => {
    catalogOk = true;
    assignedCourses = [{ course_id: 2, title: "KUCHIPUDI MALAYALM UNIT2", role: "Enrolled" }];
    alertMock = vi.fn();
    vi.stubGlobal("alert", alertMock);
    fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
      const url = new URL(input);
      const path = url.pathname;
      if (path === "/api/users/admin-users/42/") {
        return json({ id: 42, username: "teststudent", first_name: "Test", last_name: "Student", is_student: true, student_type: "REGULAR" });
      }
      if (path === "/api/users/admin-users/42/courses/") return json(assignedCourses);
      if (path === "/api/courses/") return catalogOk ? json(CATALOG) : json({ detail: "boom" }, 500);
      if (path === "/api/users/admin-users/42/enroll_course/" && init?.method === "POST") {
        const { course_id } = JSON.parse(String(init.body));
        const course = CATALOG.find((c) => c.id === course_id)!;
        assignedCourses = [...assignedCourses, { course_id, title: course.title, role: "Enrolled" }];
        return json({ message: "ok" });
      }
      if (path === "/api/courses/live-classes/") return json({ results: [] });
      return json([]);
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  const catalogCalls = () => fetchMock.mock.calls.filter(([u]) => new URL(u).pathname === "/api/courses/").length;

  it("searches the catalog, assigns the picked course and refreshes the assigned list", async () => {
    render(<UserDetailPage />);
    fireEvent.click(await screen.findByRole("button", { name: /Assign Course/ }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Test Student")).toBeInTheDocument();
    expect(await within(dialog).findByText("7 courses")).toBeInTheDocument();

    fireEvent.change(within(dialog).getByRole("combobox"), { target: { value: "kuchipudi english" } });
    fireEvent.click(within(dialog).getByRole("option", { name: /KUCHIPUDI ENGLISH UNIT 2/ }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Assign Access" }));

    await waitFor(() => expect(alertMock).toHaveBeenCalledWith("Course successfully enrolled!"));
    const enrollCalls = fetchMock.mock.calls.filter(([u]) => String(u).endsWith("/enroll_course/"));
    expect(enrollCalls).toHaveLength(1);
    expect(enrollCalls[0][0]).toBe(`${API}/api/users/admin-users/42/enroll_course/`);
    expect(JSON.parse(String(enrollCalls[0][1].body))).toEqual({ course_id: 4 });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(await screen.findByText("KUCHIPUDI ENGLISH UNIT 2")).toBeInTheDocument();

    // The catalog is loaded once and reused; reopening starts with a fresh search.
    fireEvent.click(screen.getByRole("button", { name: /Assign Course/ }));
    const reopened = await screen.findByRole("dialog");
    expect(within(reopened).getByRole("combobox")).toHaveValue("");
    expect(within(reopened).getAllByText(/Already assigned ·/)).toHaveLength(2);
    expect(catalogCalls()).toBe(1);
  });

  it("shows an error when the catalog fails to load, and retries on request", async () => {
    catalogOk = false;
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(<UserDetailPage />);
    fireEvent.click(await screen.findByRole("button", { name: /Assign Course/ }));

    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("Couldn't load the course list.")).toBeInTheDocument();

    catalogOk = true;
    fireEvent.click(within(dialog).getByRole("button", { name: "Try again" }));
    expect(await within(dialog).findByText("7 courses")).toBeInTheDocument();
    expect(catalogCalls()).toBe(2);
  });
});
