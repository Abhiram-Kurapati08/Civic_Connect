import { Link } from "react-router-dom";
import { Compass } from "lucide-react";
import { Button } from "@/components/ui/button";
import EmptyState from "@/components/EmptyState";

export default function NotFound() {
  return (
    <div className="mx-auto max-w-3xl px-4 py-20 sm:px-6">
      <EmptyState
        icon={Compass}
        title="Page not found"
        description="The page you were looking for doesn't exist. The map and every complaint are still right where you left them."
        action={<Button render={<Link to="/" />}>Back to the homepage</Button>}
      />
    </div>
  );
}
