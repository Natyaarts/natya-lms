import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import AdminUsers from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

// Synthetic data only.
describe("Admin users: Add New Student / Teacher", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let createResponse: { status: number; body: unknown };

  const json = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

  beforeEach(() => {
    createResponse = { status: 201, body: { id: 501 } };
    fetchMock = vi.fn(async (_input: string, init?: RequestInit) => {
      if (init?.method === "POST") return json(createResponse.body, createResponse.status);
      return json({ count: 0, next: null, results: [] });
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const createCalls = () => fetchMock.mock.calls.filter(([, init]) => init?.method === "POST");
  const lastPayload = () => JSON.parse(String(createCalls().at(-1)![1].body));

  const openAddForm = async (tab?: "Teachers" | "Mentors") => {
    render(<AdminUsers />);
    if (tab) fireEvent.click(screen.getByRole("button", { name: tab }));
    fireEvent.click(await screen.findByRole("button", { name: /^Add (Student|Teacher|Mentor)$/ }));
  };

  it("student form has no username or password, and explains OTP login", async () => {
    await openAddForm();
    expect(screen.getByRole("heading", { name: "Add New Student" })).toBeInTheDocument();
    expect(screen.getByText(/sign in with mobile number and OTP/)).toBeInTheDocument();
    expect(screen.queryByPlaceholderText("e.g. johndoe")).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText("Leave blank for OTP-only")).not.toBeInTheDocument();
    expect(document.querySelector('input[type="password"]')).toBeNull();
  });

  it("creates the student with the phone number as login name and no password", async () => {
    await openAddForm();
    fireEvent.change(screen.getByPlaceholderText("e.g. 8113900860"), { target: { value: "81139 00860" } });
    fireEvent.change(screen.getByPlaceholderText("e.g. John"), { target: { value: "Test" } });
    fireEvent.click(screen.getByRole("button", { name: "Create Account" }));

    await waitFor(() => expect(createCalls()).toHaveLength(1));
    const payload = lastPayload();
    expect(payload.username).toBe("+918113900860");
    expect(payload.phone_number).toBe("+918113900860");
    expect(payload).not.toHaveProperty("password");
    expect(payload).toMatchObject({ is_student: true, is_teacher: false, is_mentor: false, is_superuser: false, first_name: "Test" });
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Add New Student" })).not.toBeInTheDocument());
  });

  it("asks for the mobile number instead of sending a student without one", async () => {
    await openAddForm();
    fireEvent.submit(screen.getByRole("button", { name: "Create Account" }).closest("form")!);
    expect(await screen.findByText("Enter the student's mobile number.")).toBeInTheDocument();
    expect(createCalls()).toHaveLength(0);
  });

  it("shows the phone message once when the number is already linked", async () => {
    createResponse = {
      status: 400,
      body: {
        username: ["A user with that username already exists."],
        phone_number: ["This mobile number is already linked to another account."],
      },
    };
    await openAddForm();
    fireEvent.change(screen.getByPlaceholderText("e.g. 8113900860"), { target: { value: "8113900860" } });
    fireEvent.click(screen.getByRole("button", { name: "Create Account" }));
    expect(await screen.findByText("phone_number: This mobile number is already linked to another account.")).toBeInTheDocument();
    expect(screen.queryByText(/A user with that username/)).not.toBeInTheDocument();
    expect(screen.queryByText(/already used by another account/)).not.toBeInTheDocument();
  });

  it("turns a login-name clash into a plain phone message", async () => {
    createResponse = { status: 400, body: { username: ["A user with that username already exists."] } };
    await openAddForm();
    fireEvent.change(screen.getByPlaceholderText("e.g. 8113900860"), { target: { value: "8113900860" } });
    fireEvent.click(screen.getByRole("button", { name: "Create Account" }));
    expect(
      await screen.findByText("This mobile number (+918113900860) is already used by another account. Search for it in the user list."),
    ).toBeInTheDocument();
  });

  it("teacher form keeps username and password, with browser autofill switched off", async () => {
    await openAddForm("Teachers");
    expect(screen.getByRole("heading", { name: "Add New Teacher" })).toBeInTheDocument();
    const username = screen.getByPlaceholderText("e.g. johndoe");
    const password = screen.getByPlaceholderText("Leave blank for OTP-only");
    expect(username).toHaveAttribute("autocomplete", "off");
    expect(password).toHaveAttribute("autocomplete", "new-password");

    fireEvent.change(username, { target: { value: "  teacher_one " } });
    fireEvent.change(password, { target: { value: "Synthetic-Pass-123" } });
    fireEvent.change(screen.getByPlaceholderText("e.g. 8113900860"), { target: { value: "8113900861" } });
    fireEvent.click(screen.getByRole("button", { name: "Create Account" }));

    await waitFor(() => expect(createCalls()).toHaveLength(1));
    expect(lastPayload()).toMatchObject({
      username: "teacher_one",
      password: "Synthetic-Pass-123",
      phone_number: "+918113900861",
      is_teacher: true,
      is_student: false,
    });
  });

  it("a password typed for a teacher is never sent for a student", async () => {
    await openAddForm("Teachers");
    fireEvent.change(screen.getByPlaceholderText("Leave blank for OTP-only"), { target: { value: "Synthetic-Pass-123" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "Students" }));
    fireEvent.click(await screen.findByRole("button", { name: "Add Student" }));
    fireEvent.change(screen.getByPlaceholderText("e.g. 8113900860"), { target: { value: "8113900862" } });
    fireEvent.click(screen.getByRole("button", { name: "Create Account" }));

    await waitFor(() => expect(createCalls()).toHaveLength(1));
    expect(lastPayload()).not.toHaveProperty("password");
    expect(lastPayload().username).toBe("+918113900862");
  });

  it("fields are labelled, and the learning track is a two-option choice", async () => {
    await openAddForm();
    expect(screen.getByLabelText(/Mobile number/)).toHaveAttribute("placeholder", "e.g. 8113900860");
    expect(screen.getByLabelText("First name")).toHaveAttribute("placeholder", "e.g. John");
    expect(screen.getByLabelText(/Email address/)).toHaveAttribute("type", "email");
    expect(screen.getByText("Used for OTP sign-in.")).toBeInTheDocument();

    const career = screen.getByRole("radio", { name: /Career Academy/ });
    const regular = screen.getByRole("radio", { name: /Regular Track/ });
    expect(career).toBeChecked();
    fireEvent.click(regular);
    expect(regular).toBeChecked();
    expect(career).not.toBeChecked();

    fireEvent.change(screen.getByLabelText(/Mobile number/), { target: { value: "8113900863" } });
    fireEvent.click(screen.getByRole("button", { name: "Create Account" }));
    await waitFor(() => expect(createCalls()).toHaveLength(1));
    expect(lastPayload().student_type).toBe("REGULAR");
  });

  it("Escape, the close button and Cancel close the form", async () => {
    await openAddForm();
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Add Student" }));
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Add Student" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});
