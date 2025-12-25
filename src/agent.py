import logging
import json
import asyncio
import aiohttp

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    RunContext,
    cli,
    inference,
    room_io,
    function_tool,
)
from livekit.plugins import noise_cancellation, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

logger = logging.getLogger("agent")

load_dotenv(".env.local")

# N8N Webhook for student feedback
N8N_FEEDBACK_URL = "https://vahith.app.n8n.cloud/webhook/student-feedback"


AGENT_INSTRUCTION = """
# Persona 
You are Liya, a Student Project Guide AI Assistant and Project Examiner that helps students worldwide build projects step-by-step and evaluates their knowledge.

#Context
You are a multilingual virtual assistant that provides comprehensive project guidance to students from all countries and educational backgrounds. You communicate in the student's preferred language and adapt to their skill level.

# Capabilities
You have the following special abilities:
1. **Quiz Mode (Viva Voice)** - Test student knowledge about their project with questions
2. **Project Review** - Review student presentations (PPT) and provide feedback
3. **Feedback System** - Submit student performance feedback to the evaluation system

# Task
Provide detailed, step-by-step project guidance AND evaluate student performance:

    ## Project Guidance
    1. Help students choose appropriate projects based on skill level
    2. Break down complex projects into manageable phases
    3. Provide step-by-step implementation instructions
    4. Help with debugging and troubleshooting

    ## Quiz/Viva Mode
    When conducting a viva or quiz:
    1. Ask questions about the student's project
    2. Test understanding of concepts, technologies, and implementation
    3. Ask about challenges faced and solutions found
    4. Evaluate problem-solving skills
    5. Use the submit_feedback tool to record performance

    Question types to ask:
    - What problem does your project solve?
    - Explain the architecture/design of your project
    - What technologies did you use and why?
    - What challenges did you face?
    - How would you improve your project?
    - Explain a specific feature implementation
    
    ## Project Review
    When reviewing a project or presentation:
    1. Ask the student to share their screen showing PPT/presentation
    2. Review each slide for content, design, and clarity
    3. Evaluate technical accuracy
    4. Provide constructive feedback
    5. Submit feedback using the feedback tool

# Voice Output Guidelines
- Keep responses concise for voice (1-3 sentences at a time)
- Use natural conversational tone
- Be encouraging but honest in feedback
- Celebrate achievements and provide constructive criticism
"""


class LiyaAssistant(Agent):
    def __init__(self, student_context: dict = None) -> None:
        self.student_context = student_context or {}
        self.quiz_score = 0
        self.questions_asked = 0
        self.feedback_notes = []
        
        # Build personalized instructions based on student context
        personalized_instructions = AGENT_INSTRUCTION
        
        if student_context:
            context_info = f"""
# Student Information
- Name: {student_context.get('name', 'Student')}
- Email: {student_context.get('email', 'Not provided')}
- Skill Level: {student_context.get('skillLevel', 'Beginner')}
- Interests: {', '.join(student_context.get('interests', []))}
"""
            
            projects = student_context.get('activeProjects', [])
            if projects:
                for project in projects:
                    context_info += f"- {project.get('title')} ({project.get('domain')}): {project.get('progress')}% complete.\n"
            else:
                context_info += "- No active projects yet\n"
            
            personalized_instructions += context_info
        
        super().__init__(instructions=personalized_instructions)

    @function_tool
    async def record_pedagogical_observation(self, context: RunContext, observation: str, urgency: str):
        """Record an observation about the student's learning progress or behavior during the session.
        
        Args:
            observation: The specific observation to record
        """
        self.feedback_notes.append(f"Observation: {observation}")
        logger.info(f"Recorded pedagogical observation: {observation}")
        return "Observation recorded for the final report."

    @function_tool
    async def start_quiz(self, context: RunContext, project_name: str):
        """Start a quiz/viva session to test student knowledge about their project.
        
        Args:
            project_name: The name of the project to quiz about
        """
        self.quiz_score = 0
        self.questions_asked = 0
        logger.info(f"Starting quiz for project: {project_name}")
        return f"Starting viva session for project: {project_name}. I will ask you questions about your project. Answer honestly and explain your understanding."

    @function_tool
    async def record_quiz_answer(self, context: RunContext, question: str, answer_quality: str, notes: str):
        """Record a student's answer quality during the quiz.
        
        Args:
            question: The question that was asked
            answer_quality: Rating of the answer - 'excellent', 'good', 'satisfactory', 'needs_improvement', 'incorrect'
            notes: Additional notes about the answer
        """
        self.questions_asked += 1
        score_map = {'excellent': 10, 'good': 8, 'satisfactory': 6, 'needs_improvement': 4, 'incorrect': 2}
        self.quiz_score += score_map.get(answer_quality, 5)
        self.feedback_notes.append(f"Q{self.questions_asked}: {question} - {answer_quality}: {notes}")
        logger.info(f"Recorded answer: {answer_quality} for question: {question}")
        return f"Answer recorded. Quality: {answer_quality}"

    @function_tool
    async def update_project_status(self, context: RunContext, project_id: str, status: str, progress: int):
        """Update the status and progress of a student's project in the dashboard.
        Use this when a student completes a phase, starts a new one, or makes significant progress.
        
        Args:
            project_id: The unique ID of the project to update (from student_context)
            status: New status - 'planning', 'in-progress', 'completed', 'paused'
            progress: New progress percentage (0-100)
        """
        student_name = self.student_context.get('name', 'Student')
        logger.info(f"Liya is updating project {project_id} for {student_name} -> {status} ({progress}%)")
        
        # Send a data message to the frontend to trigger the database update
        try:
            payload = {
                "type": "update_project_status",
                "data": {
                    "projectId": project_id,
                    "status": status,
                    "progress": progress
                }
            }
            
            # Use reliable data transfer
            await context.room.local_participant.publish_data(
                json.dumps(payload),
                reliable=True
            )
            logger.info(f"✓ Project update signal sent to rooms")
        except Exception as e:
            logger.error(f"✗ Failed to send project update signal: {e}")
            return f"I tried to update your project status but encountered a technical issue: {e}"

        return f"I've updated your project status in the dashboard to '{status}' with {progress}% progress. Great work!"

    @function_tool
    async def submit_feedback(self, context: RunContext, overall_rating: str, strengths: str, improvements: str, recommendation: str):
        """Submit student feedback to the evaluation system via n8n webhook.
        This will also trigger an email to be sent to the student with their feedback.
        
        Args:
            overall_rating: Overall rating - 'excellent', 'good', 'satisfactory', 'needs_improvement'
            strengths: Key strengths observed in the student's work
            improvements: Areas that need improvement
            recommendation: Final recommendation or comments
        """
        student_name = self.student_context.get('name', 'Unknown Student')
        student_email = self.student_context.get('email', '')
        skill_level = self.student_context.get('skillLevel', 'Not specified')
        interests = self.student_context.get('interests', [])
        
        # Get project info
        projects = self.student_context.get('activeProjects', [])
        project_info = None
        if projects:
            project = projects[0]
            project_info = {
                "title": project.get('title', 'Unknown Project'),
                "domain": project.get('domain', 'General'),
                "progress": project.get('progress', 0),
                "current_phase": project.get('currentPhase', 'Not started'),
                "technologies": project.get('technologies', [])
            }
        
        # Calculate final score
        avg_score = (self.quiz_score / max(self.questions_asked, 1)) if self.questions_asked > 0 else 0
        
        # Grade mapping
        if avg_score >= 9:
            grade = "A+"
        elif avg_score >= 8:
            grade = "A"
        elif avg_score >= 7:
            grade = "B"
        elif avg_score >= 6:
            grade = "C"
        elif avg_score >= 5:
            grade = "D"
        else:
            grade = "F"
        
        from datetime import datetime
        
        feedback_data = {
            # Flag to tell n8n to send email
            "send_email": True,
            
            # Student Information
            "student_name": student_name,
            "student_email": student_email,
            "skill_level": skill_level,
            "interests": interests,
            "convex_user_id": self.student_context.get('convexId'),
            
            # Project Information
            "project": project_info,
            "project_name": project_info["title"] if project_info else "No Project",
            
            # Quiz Results
            "overall_rating": overall_rating,
            "quiz_score": round(avg_score, 2),
            "grade": grade,
            "questions_asked": self.questions_asked,
            "total_points": self.quiz_score,
            
            # Feedback Details
            "strengths": strengths,
            "improvements": improvements,
            "recommendation": recommendation,
            "detailed_notes": self.feedback_notes,
            
            # Metadata
            "feedback_type": "quiz_viva",
            "evaluator": "Liya AI Coach",
            "timestamp": datetime.now().isoformat(),
            "date": datetime.now().strftime("%B %d, %Y"),
            "time": datetime.now().strftime("%I:%M %p")
        }
        
        logger.info(f"--- N8N FEEDBACK SUBMISSION ---")
        logger.info(f"Student: {student_name} <{student_email}>")
        logger.info(f"Rating: {overall_rating} | Grade: {grade} | Score: {avg_score:.2f}")
        logger.info(f"Payload: {json.dumps(feedback_data, indent=2)}")
        
        # 1. Send to n8n Webhook (Email)
        webhook_status = "Pending"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(N8N_FEEDBACK_URL, json=feedback_data) as response:
                    if response.status == 200:
                        webhook_status = "Success"
                        logger.info("✓ n8n Webhook: Connection successful and data delivered")
                    else:
                        webhook_status = f"Failed ({response.status})"
                        logger.error(f"✗ n8n Webhook Error: Status {response.status}")
        except Exception as e:
            webhook_status = f"Error: {e}"
            logger.error(f"✗ n8n Webhook Exception: {e}")

        return f"Feedback synchronized! Grade: {grade}, Score: {avg_score:.1f}/10. (Target: {student_email})"


    @function_tool
    async def review_presentation(self, context: RunContext, slide_content: str, feedback: str):
        """Review a presentation slide and provide feedback.
        
        Args:
            slide_content: Description of what's on the current slide
            feedback: Feedback for this slide
        """
        self.feedback_notes.append(f"Slide Review: {slide_content} - {feedback}")
        logger.info(f"Reviewing slide: {slide_content}")
        return f"Slide reviewed: {feedback}"

    @function_tool
    async def end_quiz_session(self, context: RunContext):
        """End the quiz session and provide a summary.
        """
        if self.questions_asked == 0:
            return "No questions were asked in this session."
        
        avg_score = self.quiz_score / self.questions_asked
        grade = "Excellent" if avg_score >= 8 else "Good" if avg_score >= 6 else "Satisfactory" if avg_score >= 4 else "Needs Improvement"
        
        summary = f"Quiz completed! Questions asked: {self.questions_asked}, Average score: {avg_score:.1f}/10, Grade: {grade}"
        logger.info(summary)
        return summary

    @function_tool
    async def test_n8n_webhook(self, context: RunContext):
        """Test the n8n webhook connection to verify it's working correctly.
        Use this when the user asks to test the n8n connection or webhook.
        """
        student_name = self.student_context.get('name', 'Test Student')
        
        test_data = {
            "test": True,
            "student_name": student_name,
            "message": "This is a test from Liya AI Coach",
            "timestamp": str(asyncio.get_event_loop().time())
        }
        
        logger.info(f"Testing n8n webhook: {N8N_FEEDBACK_URL}")
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(N8N_FEEDBACK_URL, json=test_data, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    status = response.status
                    response_text = await response.text()
                    
                    if status == 200:
                        logger.info(f"N8N webhook test SUCCESS! Response: {response_text}")
                        return f"Great news! The n8n webhook is working correctly. Status: {status}. The feedback system is ready to receive data."
                    else:
                        logger.warning(f"N8N webhook returned status {status}: {response_text}")
                        return f"The n8n webhook responded with status {status}. This might indicate a configuration issue."
        except asyncio.TimeoutError:
            logger.error("N8N webhook test timed out")
            return "The n8n webhook test timed out. The server might be slow or unreachable."
        except Exception as e:
            logger.error(f"N8N webhook test failed: {e}")
            return f"The n8n webhook test failed with error: {str(e)}. Please check the webhook URL configuration."


server = AgentServer()

# Store student context per room
room_contexts = {}


def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session()
async def my_agent(ctx: JobContext):
    logger.info(f"--- AGENT SESSION STARTED IN ROOM: {ctx.room.name} ---")
    # Logging setup
    ctx.log_context_fields = {
        "room": ctx.room.name,
    }
    
    # Store for student context
    student_context = {"received": False, "data": None}
    session = None

    # Handle incoming data messages (student context and text messages)
    def on_data_received(data: rtc.DataPacket):
        logger.info("*******************************************")
        logger.info("********** DATA RECEIVED START ************")
        logger.info("*******************************************")
        try:
            payload = data.data.decode("utf-8")
            logger.info(f"PAYLOAD: {payload}")
            message = json.loads(payload)
            msg_type = message.get("type")
            logger.info(f"MESSAGE TYPE: {msg_type}")
            
            if msg_type == "student_context":
                student_context["data"] = message.get("context", {})
                student_context["received"] = True
                logger.info(f"STUDENT CONTEXT RECEIVED FOR: {student_context['data'].get('name')}")
                
            elif msg_type == "text" and message.get("text"):
                text = message["text"]
                logger.info(f"TEXT RECEIVED FROM FRONTEND: {text}")
                if session:
                    try:
                        logger.info(f"Triggering response for: {text} (Session: {session})")
                        task = asyncio.create_task(session.generate_reply(user_input=text))
                        
                        def task_done(t):
                            try:
                                t.result()
                                logger.info(f"✓ SUCCESSFULLY generated reply for: {text}")
                            except Exception as ex:
                                import traceback
                                logger.error(f"✘ FAILED to generate reply for: {text}")
                                logger.error(traceback.format_exc())
                        
                        task.add_done_callback(task_done)
                    except Exception as e:
                        import traceback
                        logger.error(f"ERROR processing text message: {e}")
                        logger.error(traceback.format_exc())
                else:
                    logger.warning("SESSION NOT READY")
            
            logger.info("*******************************************")
            logger.info("*********** DATA RECEIVED END *************")
            logger.info("*******************************************")
                
        except Exception as e:
            logger.error(f"FATAL ERROR in on_data_received: {e}")

    ctx.room.on("data_received", on_data_received)

    # Handle video track subscriptions
    def on_track_subscribed(
        track: rtc.Track,
        publication: rtc.RemoteTrackPublication,
        participant: rtc.RemoteParticipant,
    ):
        if track.kind == rtc.TrackKind.KIND_VIDEO:
            source = publication.source
            if source == rtc.TrackSource.SOURCE_SCREEN_SHARE:
                logger.info(f"Screen share started by {participant.identity} - Ready for presentation review")
            elif source == rtc.TrackSource.SOURCE_CAMERA:
                logger.info(f"Camera enabled by {participant.identity}")

    ctx.room.on("track_subscribed", on_track_subscribed)

    # Connect first to receive student context
    await ctx.connect()
    
    # Wait for student context to arrive from frontend
    logger.info("Waiting for student context from frontend...")
    for i in range(10):  # Wait up to 5 seconds
        await asyncio.sleep(0.5)
        if student_context.get("received"):
            logger.info(f"Student context received! Student: {student_context['data'].get('name', 'Unknown')}")
            logger.info(f"Skill level: {student_context['data'].get('skillLevel', 'Not set')}")
            logger.info(f"Technologies: {student_context['data'].get('preferredTechnologies', [])}")
            break
    
    if not student_context.get("received"):
        logger.warning("No student context received after 5 seconds. Proceeding without context.")
    
    # Create agent with personalized context
    logger.info(f"Creating Liya with context: {student_context.get('data')}")
    agent = LiyaAssistant(student_context=student_context.get("data"))
    # Set up a voice AI pipeline
    session = AgentSession(
        stt=inference.STT(model="assemblyai/universal-streaming", language="en"),
        llm=inference.LLM(model="google/gemini-2.0-flash"),
        tts=inference.TTS(
            model="cartesia/sonic-3", voice="9626c31c-bec5-4cca-baa8-f8ba9e84c8bc"
        ),
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata["vad"],
        preemptive_generation=True,
    )

    # Start the session
    try:
        logger.info("Starting agent session...")
        await session.start(
            agent=agent,
            room=ctx.room,
            room_options=room_io.RoomOptions(
                audio_input=room_io.AudioInputOptions(
                    noise_cancellation=lambda params: noise_cancellation.BVCTelephony()
                    if params.participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
                    else noise_cancellation.BVC(),
                ),
            ),
        )
        logger.info("Agent session started successfully")
    except Exception as e:
        logger.error(f"Failed to start agent session: {e}")
        return
    
    # Send automatic welcome greeting
    await asyncio.sleep(1.0) # Wait a bit longer for audio to bridge
    
    student_name = "there"
    if student_context.get("data"):
        student_name = student_context["data"].get("name", "there")
    
    active_projects = []
    if student_context.get("data"):
        active_projects = student_context["data"].get("activeProjects", [])
    
    if active_projects:
        project_name = active_projects[0].get("title", "your project")
        welcome_msg = f"Hi {student_name}! I'm Liya, your AI Project Coach. I see you're working on {project_name}. I can help you with project guidance, conduct a viva to test your knowledge, or review your presentation. What would you like to do?"
    else:
        welcome_msg = f"Hi {student_name}! I'm Liya, your AI Project Coach. I can help you build projects, conduct viva sessions to test your knowledge, or review your presentations. What would you like to work on today?"
    
    logger.info(f"Sending welcome: {welcome_msg}")
    try:
        await session.say(welcome_msg, allow_interruptions=True)
        logger.info("Welcome message sent")
    except Exception as e:
        logger.error(f"Failed to send welcome message: {e}")


if __name__ == "__main__":
    cli.run_app(server)
