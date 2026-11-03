"""Rendered dashboard rehearsal; every observation is explicitly scripted."""
import sys
from pathlib import Path
from uuid import uuid4
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import streamlit as st
from src.config import load_config
from src.dashboard.session import DashboardSession
from src.simulation.assembly_env import AssemblyEnv
from src.simulation.rehearsal import RehearsalInspector
from src.simulation.runner import ScenarioRunner
from src.simulation.scenarios import NAMES


def start_episode(name):
    old=st.session_state.get('episode')
    if old:
        old.close()
    session=DashboardSession(directory='outputs/dashboard-rehearsal', mode='real',
        environment=AssemblyEnv(load_config(),seed=2026), inspector=RehearsalInspector(name))
    session.scenario_runner=ScenarioRunner(name,session.environment,session.workflow,
                                         session.directory/('frames-'+uuid4().hex))
    session.render()
    st.session_state.episode=session


@st.fragment(run_every=2)
def playback():
    session=st.session_state.get('episode')
    if not session:
        st.info('Choose a scenario or play all four.')
        return
    runner=session.scenario_runner
    if st.session_state.get('playing'):
        if runner.done:
            st.session_state.history.append({'scenario':runner.name,
                'completed':runner.result.completed,'decisions':runner.result.decisions,
                'error':runner.result.error})
            queue=st.session_state.queue
            if queue and not runner.result.error:
                start_episode(queue.pop(0))
                st.rerun(scope='fragment')
            st.session_state.playing=False
        else:
            runner.tick()
            session.frame=runner.frame
    st.subheader(runner.name.replace('_',' ').title())
    status=session.workflow.state.value
    if runner.result.error:
        st.error(runner.result.error)
    elif status=='blocked':
        st.error(session.workflow.last_decision.message)
    elif runner.result.completed:
        st.success('Assembly complete — every step authorized by the workflow.')
    else:
        st.info(f'Workflow: {status} | step {session.workflow.current_step_id}')
    if session.frame:
        st.image(str(session.frame),width=640,caption='Actual PyBullet render; observations are prewritten test fixtures.')
    st.write('Decisions:', ' → '.join(runner.result.decisions) or 'Ready')
    st.caption('Wrong-order guard rejected the premature action: '+str(runner.result.guard_blocked))
    st.caption(f'Audit: {session.audit.path}')
    with st.expander('Audit events'):
        st.json(session.audit.read_all())
    if st.session_state.history:
        st.subheader('Finished episodes')
        st.dataframe(st.session_state.history)


def main():
    st.set_page_config(page_title='HoloGuide rendered rehearsal',layout='wide')
    st.title('HoloGuide — rendered scenario dashboard')
    st.warning('SCRIPTED REHEARSAL — real simulation and workflow, prewritten observations. No AI inference or accuracy claims.')
    if 'history' not in st.session_state:
        st.session_state.history=[]
        st.session_state.queue=[]
        st.session_state.playing=False
    selected=st.selectbox('Scenario',NAMES)
    if st.button('Play selected scenario'):
        st.session_state.queue=[]
        st.session_state.playing=True
        start_episode(selected)
    if st.button('Play all four scenarios'):
        st.session_state.history=[]
        st.session_state.queue=list(NAMES[1:])
        st.session_state.playing=True
        start_episode(NAMES[0])
    if st.button('Pause'):
        st.session_state.playing=False
    playback()


if __name__=='__main__':
    main()
