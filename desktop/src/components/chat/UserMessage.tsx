interface UserMessageProps {
  text: string;
}

export function UserMessage({ text }: UserMessageProps) {
  return (
    <div className="flex w-full justify-end">
      <div className="max-w-[75%] rounded-lg rounded-tr-sm bg-primary px-3.5 py-2 text-sm text-primary-foreground shadow-sm">
        {text}
      </div>
    </div>
  );
}
