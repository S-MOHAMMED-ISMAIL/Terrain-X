// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  Badge,
  Button,
  Checkbox,
  ConfirmDialog,
  CoordinateValue,
  DataState,
  Field,
  IconButton,
  Input,
  LiveStatus,
  MetricValue,
  Notice,
  Switch,
  Tabs,
  TechnicalKeyValue,
  Tooltip,
} from ".";

afterEach(cleanup);

describe("Button and icon button", () => {
  it("is keyboard focusable and preserves an accessible name", async () => {
    const user = userEvent.setup();
    render(<Button>Run analysis</Button>);

    await user.tab();
    expect(screen.getByRole("button", { name: "Run analysis" })).toHaveFocus();
  });

  it("makes disabled and loading states non-interactive and announced", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    const { rerender } = render(
      <Button disabled onClick={onClick}>
        Delete
      </Button>,
    );
    await user.click(screen.getByRole("button", { name: "Delete" }));
    expect(onClick).not.toHaveBeenCalled();

    rerender(
      <Button loading loadingLabel="Generating report" onClick={onClick}>
        Generate report
      </Button>,
    );
    const loading = screen.getByRole("button", { name: "Generating report" });
    expect(loading).toBeDisabled();
    expect(loading).toHaveAttribute("aria-busy", "true");
  });

  it("requires and exposes an accessible name for icon-only controls", () => {
    render(<IconButton aria-label="Close panel" icon={<span>X</span>} />);
    expect(screen.getByRole("button", { name: "Close panel" })).toBeVisible();
  });
});

describe("Field and form controls", () => {
  it("associates label, description, required state, and error with its input", () => {
    render(
      <Field
        id="elevation"
        label="Elevation"
        description="Reference elevation in metres."
        error="Enter a finite value."
        required
      >
        {(props) => <Input {...props} type="number" />}
      </Field>,
    );

    const input = screen.getByRole("spinbutton", { name: /Elevation/ });
    expect(input).toHaveAttribute("id", "elevation");
    expect(input).toBeRequired();
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAccessibleDescription(
      "Reference elevation in metres. Enter a finite value.",
    );
  });

  it("supports keyboard operation for checkbox and switch", async () => {
    const user = userEvent.setup();
    const onSwitch = vi.fn();
    render(
      <>
        <Field id="texture" label="Embed texture">
          {(props) => <Checkbox {...props} />}
        </Field>
        <Switch aria-label="Show grid" checked={false} onCheckedChange={onSwitch} />
      </>,
    );

    await user.click(screen.getByLabelText("Embed texture"));
    expect(screen.getByLabelText("Embed texture")).toBeChecked();
    screen.getByRole("switch", { name: "Show grid" }).focus();
    await user.keyboard(" ");
    expect(onSwitch).toHaveBeenCalledWith(true);
  });
});

describe("Tabs", () => {
  it("uses tab semantics, arrow-key navigation, and the selected panel", async () => {
    const user = userEvent.setup();
    render(
      <Tabs
        ariaLabel="Terrain sections"
        items={[
          { id: "layers", label: "Layers", panel: <p>Layer panel</p> },
          { id: "disabled", label: "Unavailable", panel: <p>Unavailable panel</p>, disabled: true },
          { id: "details", label: "Details", panel: <p>Detail panel</p> },
        ]}
      />,
    );

    const layers = screen.getByRole("tab", { name: "Layers" });
    expect(layers).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("Layer panel");

    layers.focus();
    await user.keyboard("{ArrowRight}");
    const details = screen.getByRole("tab", { name: "Details" });
    expect(details).toHaveFocus();
    expect(details).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("Detail panel");
  });
});

describe("Tooltip", () => {
  it("has an accessible relationship and opens from keyboard focus", async () => {
    const user = userEvent.setup();
    render(
      <Tooltip content="Reset the camera to fit the terrain">
        <button type="button">Reset view</button>
      </Tooltip>,
    );

    const trigger = screen.getByRole("button", { name: "Reset view" });
    const tooltip = screen.getByRole("tooltip", { hidden: true });
    expect(trigger).toHaveAttribute("aria-describedby", tooltip.id);
    expect(tooltip).not.toBeVisible();

    await user.tab();
    expect(tooltip).toBeVisible();
    await user.keyboard("{Escape}");
    expect(tooltip).not.toBeVisible();
  });
});

describe("Dialog and confirmation", () => {
  it("moves focus in, closes on Escape, and restores trigger focus", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();

    function Harness() {
      return (
        <>
          <button type="button">Open dialog</button>
          <ConfirmDialog
            open
            onClose={onClose}
            onConfirm={() => undefined}
            title="Delete project?"
            description="This removes the project and its datasets."
          />
        </>
      );
    }

    render(<Harness />);
    expect(screen.getByRole("dialog", { name: "Delete project?" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("restores focus when the controlled dialog actually closes", async () => {
    const user = userEvent.setup();

    function Harness() {
      const [open, setOpen] = React.useState(false);
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>
            Delete dataset
          </button>
          <ConfirmDialog
            open={open}
            onClose={() => setOpen(false)}
            onConfirm={() => setOpen(false)}
            title="Delete dataset?"
            description="This action cannot be undone."
          />
        </>
      );
    }

    const React = await import("react");
    render(<Harness />);
    const trigger = screen.getByRole("button", { name: "Delete dataset" });
    await user.click(trigger);
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});

describe("Status and engineering information", () => {
  it("keeps semantic status text visible without depending on color", () => {
    render(
      <>
        <Notice tone="warning" title="Calibration rejected">
          Relative terrain remains available.
        </Notice>
        <Badge tone="danger">Failed</Badge>
        <DataState state="processing" detail="Depth estimation" />
        <LiveStatus>Analysis completed</LiveStatus>
      </>,
    );

    expect(screen.getByText("Calibration rejected")).toBeVisible();
    expect(screen.getByText("Relative terrain remains available.")).toBeVisible();
    expect(screen.getByText("Failed")).toBeVisible();
    expect(screen.getByText("Processing")).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("Analysis completed");
  });

  it("renders metric, coordinate, and technical values without inventing state", () => {
    render(
      <dl>
        <MetricValue label="Elevation" value="124.82" unit="m" />
        <CoordinateValue coordinate="474720.25, 4424621.75" crs="EPSG:26913" />
        <TechnicalKeyValue label="Surface" value="DSM" />
      </dl>,
    );
    expect(screen.getByText("124.82")).toBeVisible();
    expect(screen.getByText("EPSG:26913")).toBeVisible();
    expect(screen.getByText("DSM")).toBeVisible();
  });

  it("turns badge detail into a keyboard-accessible tooltip instead of native title", async () => {
    const user = userEvent.setup();
    render(
      <Badge tone="warning" title="The quality gate did not pass.">
        Calibration rejected
      </Badge>,
    );
    const badge = screen.getByText("Calibration rejected");
    expect(badge).not.toHaveAttribute("title");
    await user.tab();
    expect(screen.getByRole("tooltip")).toHaveTextContent("The quality gate did not pass.");
  });
});
