from vercel import workflow

wf = workflow.Workflows(
    sandbox_policy=workflow.SandboxPolicy(share_sandboxes=True),
)
