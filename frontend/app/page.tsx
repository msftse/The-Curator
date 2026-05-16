import Link from "next/link";

export default function HomePage() {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Agentic Skill Hub</h1>
      <p className="text-gray-600">
        Submit a SKILL.md bundle, watch the classifier run, and have a manager publish it
        to the catalog.
      </p>
      <ul className="list-inside list-disc space-y-1 text-sm text-gray-700">
        <li>
          <Link className="text-blue-600 underline" href="/upload">
            Upload a skill
          </Link>
        </li>
        <li>
          <Link className="text-blue-600 underline" href="/my-submissions">
            See my submissions
          </Link>
        </li>
        <li>
          <Link className="text-blue-600 underline" href="/admin/queue">
            Manager review queue (requires manager@org)
          </Link>
        </li>
      </ul>
    </div>
  );
}
