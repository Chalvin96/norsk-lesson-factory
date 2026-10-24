import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { cn } from '@/lib/utils';

export interface SegmentedOption<T extends string> {
  value: T;
  label: string;
}

export interface SegmentedControlProps<T extends string> {
  label: string;
  value: T;
  options: readonly SegmentedOption<T>[];
  onValueChange: (value: T) => void;
  size?: 'sm' | 'default';
  className?: string;
}

export function SegmentedControl<T extends string>({
  label,
  value,
  options,
  onValueChange,
  size = 'default',
  className,
}: SegmentedControlProps<T>) {
  return (
    <ToggleGroup
      type="single"
      variant="outline"
      size={size}
      aria-label={label}
      value={value}
      onValueChange={(next) => {
        if (next) onValueChange(next as T);
      }}
      className={cn('shrink-0', className)}
    >
      {options.map((option) => (
        <ToggleGroupItem
          key={option.value}
          value={option.value}
          className="border-border px-3 focus-visible:outline-2 focus-visible:outline-solid focus-visible:outline-offset-2 focus-visible:outline-ring data-[state=on]:border-secondary-100 data-[state=on]:bg-secondary-100 data-[state=on]:text-white"
        >
          {option.label}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  );
}
