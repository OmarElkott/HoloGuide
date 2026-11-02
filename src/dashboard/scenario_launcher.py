"""Thin dashboard controls for the shared rendered scenario runner."""
from uuid import uuid4
from src.simulation.runner import ScenarioRunner
from src.simulation.scenarios import NAMES


def show_launcher(st, session):
    if session.mode != 'real':
        return
    st.subheader('Rendered scenario launcher')
    st.caption('Start with an empty, fresh session. Faults are deliberately injected and audited.')
    name = st.selectbox('Scenario', NAMES)
    runner = getattr(session, 'scenario_runner', None)
    if st.button('Load scenario', disabled=runner is not None or session.workflow.last_decision is not None or session.revision != 0):
        session.scenario_runner = ScenarioRunner(name, session.environment, session.workflow,
                                                session.directory / ('scenario-' + uuid4().hex))
        st.rerun()
    if runner is not None:
        if st.button('Play next scenario step', disabled=runner.done):
            with st.spinner('Rendering and inspecting scenario step…'):
                runner.tick()
            session.frame = runner.frame
            session.inspected_frame = runner.frame
            session.revision += 1
            st.rerun()
        if runner.result.error:
            st.error(runner.result.error)
        elif runner.result.completed:
            st.success('Scenario completed through the workflow.')
        st.write(runner.result.decisions)
