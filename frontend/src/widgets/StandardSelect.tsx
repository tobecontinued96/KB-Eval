import {
  Children,
  isValidElement,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type InputHTMLAttributes,
  type KeyboardEvent,
  type ReactNode,
  type SelectHTMLAttributes
} from "react";
import { ChevronDown } from "lucide-react";

type StandardSelectProps = SelectHTMLAttributes<HTMLSelectElement> & {
  tooltip?: string;
};

interface StandardOption {
  disabled: boolean;
  label: string;
  value: string;
}

function textOf(value: ReactNode): string {
  if (typeof value === "string" || typeof value === "number") return String(value);
  if (Array.isArray(value)) return value.map(textOf).join("");
  return "";
}

function optionItems(children: ReactNode): StandardOption[] {
  return Children.toArray(children).flatMap((child) => {
    if (!isValidElement<{ children?: ReactNode; disabled?: boolean; value?: string | number }>(child)) return [];
    if (child.type !== "option") return [];
    const label = textOf(child.props.children).trim();
    return [
      {
        disabled: Boolean(child.props.disabled),
        label,
        value: String(child.props.value ?? label)
      }
    ];
  });
}

export function StandardSelect({
  className = "",
  defaultValue,
  disabled,
  onChange,
  title,
  tooltip,
  value,
  children,
  ...props
}: StandardSelectProps) {
  const resolvedTitle = title || tooltip;
  const controlRef = useRef<HTMLSpanElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const [open, setOpen] = useState(false);
  const [draftValue, setDraftValue] = useState<string>(
    String(value ?? defaultValue ?? "")
  );
  const options = useMemo(() => optionItems(children), [children]);
  const selectedValue = String(value ?? draftValue ?? options[0]?.value ?? "");
  const selectedIndex = Math.max(0, options.findIndex((option) => option.value === selectedValue));
  const [highlightedIndex, setHighlightedIndex] = useState(selectedIndex);
  const selectedOption = options.find((option) => option.value === selectedValue) ?? options[0];

  useEffect(() => {
    if (value !== undefined) setDraftValue(String(value));
  }, [value]);

  useEffect(() => {
    if (!open) return;
    const handlePointerDown = (event: MouseEvent) => {
      if (!controlRef.current?.contains(event.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", handlePointerDown);
    return () => window.removeEventListener("mousedown", handlePointerDown);
  }, [open]);

  function emitChange(nextValue: string) {
    if (value === undefined) setDraftValue(nextValue);
    onChange?.({
      currentTarget: { value: nextValue },
      target: { value: nextValue }
    } as ChangeEvent<HTMLSelectElement>);
  }

  function selectOption(option: StandardOption) {
    if (disabled || option.disabled) return;
    emitChange(option.value);
    setOpen(false);
    window.requestAnimationFrame(() => triggerRef.current?.focus());
  }

  function moveHighlight(step: number) {
    if (options.length === 0) return;
    let next = highlightedIndex;
    for (let attempts = 0; attempts < options.length; attempts += 1) {
      next = (next + step + options.length) % options.length;
      if (!options[next]?.disabled) break;
    }
    setHighlightedIndex(next);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!open) {
        setOpen(true);
        setHighlightedIndex(selectedIndex);
        return;
      }
      moveHighlight(event.key === "ArrowDown" ? 1 : -1);
      return;
    }
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (!open) {
        setOpen(true);
        setHighlightedIndex(selectedIndex);
        return;
      }
      const option = options[highlightedIndex];
      if (option) selectOption(option);
      return;
    }
    if (event.key === "Escape") {
      setOpen(false);
    }
  }

  return (
    <span
      className={`standard-select-control ${className}`.trim()}
      ref={controlRef}
      title={resolvedTitle}
      data-disabled={disabled ? "true" : undefined}
    >
      <button
        className="standard-select standard-select-trigger"
        type="button"
        title={resolvedTitle}
        disabled={disabled}
        aria-label={props["aria-label"]}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => {
          if (disabled) return;
          setOpen((current) => !current);
          setHighlightedIndex(selectedIndex);
        }}
        onKeyDown={handleKeyDown}
        ref={triggerRef}
      >
        <span className="standard-select-value">{selectedOption?.label || ""}</span>
      </button>
      <span className="standard-select-arrow" aria-hidden="true">
        <ChevronDown size={15} />
      </span>
      {props.name && <input type="hidden" name={props.name} value={selectedValue} />}
      {open && options.length > 0 && (
        <div className="standard-select-panel" role="listbox">
          {options.map((option, index) => (
            <button
              key={`${option.value}-${index}`}
              type="button"
              role="option"
              aria-selected={option.value === selectedValue}
              disabled={option.disabled}
              className={`standard-select-option${option.value === selectedValue ? " is-selected" : ""}${
                index === highlightedIndex ? " is-highlighted" : ""
              }`}
              onMouseEnter={() => setHighlightedIndex(index)}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => selectOption(option)}
              title={option.label}
            >
              {option.label}
            </button>
          ))}
        </div>
      )}
    </span>
  );
}

type DatalistInputProps = InputHTMLAttributes<HTMLInputElement> & {
  datalistId: string;
  options: string[];
  tooltip?: string;
};

export function DatalistInput({
  className = "",
  datalistId,
  defaultValue,
  disabled,
  onBlur,
  onClick,
  onChange,
  onFocus,
  options,
  title,
  tooltip,
  value,
  ...props
}: DatalistInputProps) {
  const resolvedTitle = title || tooltip;
  const controlRef = useRef<HTMLSpanElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [open, setOpen] = useState(false);
  const [draftValue, setDraftValue] = useState(String(value ?? defaultValue ?? ""));
  const inputValue = String(value ?? draftValue ?? "");
  const normalizedValue = inputValue.trim().toLowerCase();
  const filteredOptions = options.filter((option) => {
    if (!normalizedValue) return true;
    return option.toLowerCase().includes(normalizedValue);
  });

  useEffect(() => {
    if (value !== undefined) setDraftValue(String(value));
  }, [value]);

  useEffect(() => {
    if (!open) return;
    const handlePointerDown = (event: MouseEvent) => {
      if (!controlRef.current?.contains(event.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", handlePointerDown);
    return () => window.removeEventListener("mousedown", handlePointerDown);
  }, [open]);

  function emitInputChange(nextValue: string) {
    if (value === undefined) setDraftValue(nextValue);
    onChange?.({
      currentTarget: { value: nextValue },
      target: { value: nextValue }
    } as ChangeEvent<HTMLInputElement>);
  }

  return (
    <span className="standard-datalist-shell">
      <span
        className={`standard-datalist-control ${className}`.trim()}
        title={resolvedTitle}
        data-disabled={disabled ? "true" : undefined}
        ref={controlRef}
      >
        <input
          className="standard-datalist-input"
          ref={inputRef}
          title={resolvedTitle}
          value={inputValue}
          defaultValue={undefined}
          disabled={disabled}
          aria-controls={datalistId}
          aria-expanded={open}
          aria-haspopup="listbox"
          onBlur={onBlur}
          onClick={(event) => {
            onClick?.(event);
            if (!disabled) setOpen(true);
          }}
          onChange={(event) => {
            emitInputChange(event.target.value);
            setOpen(true);
          }}
          onFocus={(event) => {
            onFocus?.(event);
            if (!disabled) setOpen(true);
          }}
          {...props}
        />
        <span className="standard-select-arrow" aria-hidden="true">
          <ChevronDown size={15} />
        </span>
      </span>
      {open && !disabled && filteredOptions.length > 0 && (
        <div className="standard-select-panel standard-datalist-panel" id={datalistId} role="listbox">
          {filteredOptions.map((option) => (
            <button
              key={option}
              type="button"
              role="option"
              aria-selected={option === inputValue}
              className={`standard-select-option${option === inputValue ? " is-selected" : ""}`}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => {
                emitInputChange(option);
                setOpen(false);
                window.requestAnimationFrame(() => inputRef.current?.focus());
              }}
              title={option}
            >
              {option}
            </button>
          ))}
        </div>
      )}
    </span>
  );
}
