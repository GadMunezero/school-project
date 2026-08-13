"use client";

import type { ParameterSpec } from "@/lib/types";
import { Field, Input, Select } from "@/components/ui/primitives";

/**
 * Renders inputs for an engine strategy's declared parameters.
 *
 * The specs come from `GET /strategies/engine` — the frontend never invents a parameter list, so a
 * strategy gaining a knob in the engine gains an input here without a client release. Values are
 * kept as strings and sent as strings; the server parses them into Decimals.
 */
export function ParameterFields({
  specs,
  values,
  onChange,
  idPrefix = "param",
  columns = 3,
}: {
  specs: ParameterSpec[];
  values: Record<string, string>;
  onChange: (name: string, value: string) => void;
  idPrefix?: string;
  columns?: 2 | 3;
}) {
  if (specs.length === 0) {
    return <p className="text-xs text-faint">This strategy takes no parameters.</p>;
  }

  return (
    <div className={columns === 2 ? "grid gap-3 sm:grid-cols-2" : "grid gap-3 sm:grid-cols-3"}>
      {[...specs]
        .sort((a, b) => a.display_order - b.display_order)
        .map((parameter) => {
          const id = `${idPrefix}-${parameter.name}`;
          const value = values[parameter.name] ?? parameter.default_value ?? "";

          return (
            <Field
              key={parameter.name}
              label={parameter.label ?? parameter.name}
              htmlFor={id}
              hint={parameter.description ?? undefined}
            >
              {parameter.param_type === "boolean" ? (
                <Select id={id} value={value || "True"} onChange={(event) => onChange(parameter.name, event.target.value)}>
                  <option value="True">Yes</option>
                  <option value="False">No</option>
                </Select>
              ) : parameter.param_type === "choice" ? (
                <Select id={id} value={value} onChange={(event) => onChange(parameter.name, event.target.value)}>
                  {parameter.choices.map((choice) => (
                    <option key={choice} value={choice}>
                      {choice}
                    </option>
                  ))}
                </Select>
              ) : (
                <Input
                  id={id}
                  inputMode={parameter.param_type === "string" ? "text" : "decimal"}
                  value={value}
                  min={parameter.minimum ?? undefined}
                  max={parameter.maximum ?? undefined}
                  step={parameter.step ?? undefined}
                  onChange={(event) => onChange(parameter.name, event.target.value)}
                />
              )}
            </Field>
          );
        })}
    </div>
  );
}

/** Initial form state for a spec list: every parameter at its engine-declared default. */
export function parameterDefaults(specs: ParameterSpec[]): Record<string, string> {
  const values: Record<string, string> = {};
  for (const parameter of specs) values[parameter.name] = parameter.default_value ?? "";
  return values;
}
